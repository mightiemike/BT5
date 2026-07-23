### Title
`SwapAllowlistExtension` gates by router address instead of original user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the original user. If the pool admin allowlists the router to enable router-mediated swaps for their allowlisted users, any unprivileged user can bypass the per-user allowlist entirely by routing through the router.

---

### Finding Description

**Root cause — wrong actor bound in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (enforced by `onlyPool`) and `sender` is whatever the pool passes as the first argument to `beforeSwap`.

**How the pool populates `sender`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`:

```solidity
_beforeSwap(
  msg.sender,   // ← the immediate caller of pool.swap()
  recipient,
  ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` argument to every configured extension: [3](#0-2) 

**How the router calls the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` — the router is `msg.sender` inside the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Result:** When any user routes through `MetricOmmSimpleRouter`, the extension sees `sender = router_address`. The allowlist check becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the natural step to let their allowlisted users trade via the router), **every user on-chain can bypass the per-user allowlist** by calling any router entry point.

**Contrast with `DepositAllowlistExtension`:**

The deposit allowlist correctly gates by `owner` (the position owner), not by `sender` (the caller):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [5](#0-4) 

Because `MetricOmmPoolLiquidityAdder` passes the real `owner` through to `pool.addLiquidity`, the deposit allowlist works correctly even when the liquidity adder is the immediate caller. The swap allowlist has no equivalent — it only sees the immediate caller, which is the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (private OTC pool, compliance-gated pool, etc.) is fully open to any user once the pool admin allowlists the router. Unauthorized users can execute swaps against LP capital, exposing LPs to adverse selection and value extraction that the allowlist was designed to prevent. This is a direct loss of LP principal above contest thresholds for any pool with meaningful liquidity.

Additionally, even without the bypass scenario, allowlisted users are **completely unable to use the router** unless the router is allowlisted — breaking the core swap flow for the intended user set.

---

### Likelihood Explanation

The pool admin is semi-trusted and is the natural actor who would allowlist the router. The mistake is realistic: the admin wants their allowlisted users to access the router (the primary user-facing entry point), allowlists the router address, and does not realize this opens the gate to all users. The `SwapAllowlistExtension` documentation and interface give no indication that allowlisting the router has this effect. The `DepositAllowlistExtension` behaves differently (gates by `owner`), so a pool admin who has used both extensions would have a false expectation of symmetry.

---

### Recommendation

Pass the **original user** through the swap path so the extension can gate on the economically relevant actor. Two options:

**Option A — Add a `payer`/`originator` field to the swap call:**
Extend `pool.swap()` with an explicit `originator` parameter (analogous to `owner` in `addLiquidity`) and pass it through `_beforeSwap` as a separate argument. The router sets `originator = msg.sender`.

**Option B — Check `recipient` instead of `sender` in the extension:**
For router-mediated swaps the `recipient` is the user-supplied address. This is imperfect (recipient ≠ payer in all cases) but closer to the intended actor than the router address.

The cleanest fix is Option A. The extension should then check `allowedSwapper[pool][originator]` rather than `allowedSwapper[pool][sender]`.

---

### Proof of Concept

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
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → checks allowedSwapper[pool][router] == true  ✓
  → swap proceeds — bob bypasses the allowlist
```

Direct call by a non-allowlisted user still reverts (their address is not in the allowlist), confirming the bypass is specific to the router path and is not a general open-access issue — it is triggered only when the pool admin has allowlisted the router. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
