All cited code is confirmed in the repository. The call chain is exact:

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument [1](#0-0) 
- `MetricOmmPool.swap()` passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap` [2](#0-1) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` making the router `msg.sender` inside the pool [4](#0-3) 
- `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the position owner), not `sender` (the caller) [5](#0-4) 

The asymmetry is real and the bypass path is fully reachable. The finding is valid.

---

Audit Report

## Title
`SwapAllowlistExtension` gates on immediate caller (`sender`) instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address. If the pool admin allowlists the router to enable router-mediated swaps for their intended users, every unprivileged user on-chain can bypass the per-user allowlist by routing through the router, because the extension sees `allowedSwapper[pool][router] == true` regardless of who initiated the router call.

## Finding Description
**Root cause — wrong actor bound in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool and `sender` is whatever the pool passes as the first argument to `beforeSwap`.

**How the pool populates `sender`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)` — passing the immediate caller of `pool.swap()` as `sender`. `ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

**How the router calls the pool:**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`. The router is `msg.sender` inside the pool, so `sender = router_address` reaches the extension.

**Result:** The extension checks `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the natural step to let their allowlisted users trade via the router), every user on-chain can bypass the per-user allowlist by calling any router entry point.

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the position owner), not `sender` (the caller):
```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```
Because `addLiquidity` carries an explicit `owner` parameter that is passed through to the extension, the deposit allowlist works correctly even when a liquidity adder contract is the immediate caller. The swap path has no equivalent originator parameter — it only exposes the immediate caller.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (private OTC pool, compliance-gated pool) is fully open to any user once the pool admin allowlists the router. Unauthorized users can execute swaps against LP capital, exposing LPs to adverse selection and value extraction that the allowlist was designed to prevent. This constitutes broken core pool functionality (the allowlist extension does not enforce its stated invariant) and direct loss of LP principal for any pool with meaningful liquidity. Additionally, even without the bypass scenario, allowlisted users are completely unable to use the router unless the router is allowlisted — breaking the core swap flow for the intended user set.

## Likelihood Explanation
The pool admin is semi-trusted and is the natural actor who would allowlist the router. The mistake is realistic: the admin wants their allowlisted users to access the router (the primary user-facing entry point), allowlists the router address, and does not realize this opens the gate to all users. The `SwapAllowlistExtension` documentation and interface give no indication that allowlisting the router has this effect. The `DepositAllowlistExtension` behaves differently (gates by `owner`), so a pool admin who has used both extensions would have a false expectation of symmetry. The bypass requires no special capability from the attacker — any EOA can call `MetricOmmSimpleRouter.exactInputSingle`.

## Recommendation
Pass the original user through the swap path so the extension can gate on the economically relevant actor.

**Option A (preferred) — Add an `originator` field to the swap call:** Extend `pool.swap()` with an explicit `originator` parameter (analogous to `owner` in `addLiquidity`) and pass it through `_beforeSwap` as a separate argument. The router sets `originator = msg.sender`. The extension then checks `allowedSwapper[pool][originator]` rather than `allowedSwapper[pool][sender]`.

**Option B — Check `recipient` instead of `sender` in the extension:** For router-mediated swaps the `recipient` is the user-supplied address. This is imperfect (recipient ≠ payer in all cases) but closer to the intended actor than the router address.

Option A is the cleanest fix and maintains symmetry with the deposit allowlist pattern.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = 1)
  allowedSwapper[pool][alice] = true          // alice is the intended allowlisted user
  allowedSwapper[pool][router] = true         // admin allowlists router so alice can use it

Attack (bob is NOT allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      ...
  })

  → router calls pool.swap(bob, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → ExtensionCalling._beforeSwap forwards sender=router to extension
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → checks allowedSwapper[pool][router] == true  ✓
  → swap proceeds — bob bypasses the allowlist

Verification:
  bob calls pool.swap() directly (without router)
  → pool calls _beforeSwap(msg.sender=bob, ...)
  → checks allowedSwapper[pool][bob] == false  ✗
  → reverts with NotAllowedToSwap()
```
This confirms the bypass is specific to the router path and is triggered only when the pool admin has allowlisted the router — a realistic and natural admin action.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-40)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```
