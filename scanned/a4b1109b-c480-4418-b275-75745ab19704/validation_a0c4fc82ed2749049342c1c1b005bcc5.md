### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The extension therefore checks whether the **router** is allowlisted, not the actual economic actor. Any pool admin who allowlists the router (the natural configuration for supporting router-mediated swaps) inadvertently opens the pool to every user, completely defeating the allowlist.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the `_beforeSwap` dispatcher is called with `msg.sender` as the first argument: [1](#0-0) 

`msg.sender` here is whoever called `pool.swap()` — the router when the user goes through the periphery.

**Step 2 — ExtensionCalling forwards that address as `sender` to the hook.** [2](#0-1) 

**Step 3 — SwapAllowlistExtension checks `sender` (the router) against the allowlist.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

`msg.sender` is the pool (correct). `sender` is the router address when the call originates from `MetricOmmSimpleRouter`.

**Step 4 — MetricOmmSimpleRouter calls `pool.swap()` directly, making itself `msg.sender`.** [4](#0-3) 

The router stores the real user in transient callback context for payment purposes, but the pool never sees the real user — it only sees the router.

**The broken invariant:** A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper[pool][router]`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that passes through it, so **any** user — allowlisted or not — can bypass the gate by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`.

The same structural problem exists for `DepositAllowlistExtension` in the opposite direction: it checks `owner` (position owner), not `sender` (the actual caller/payer), so a non-allowlisted user can fund a deposit into an allowlisted owner's position via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowedOwner, ...)`. [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (institutional pools, KYC-gated pools, market-maker-only pools) is fully open to any user the moment the router is allowlisted. The allowlist provides zero protection for router-mediated swaps. LP funds in the pool are exposed to unauthorized counterparties, and any fee or slippage advantage the pool was designed to reserve for specific actors is lost.

**Severity: Medium** — direct policy bypass enabling unauthorized access to restricted pools; LP assets are exposed to unintended counterparties. No direct theft of principal in a single transaction, but the protection the pool admin paid to configure is silently nullified.

---

### Likelihood Explanation

**Medium.** The router is the primary user-facing swap interface. Any pool admin who deploys a `SwapAllowlistExtension` pool and also wants allowlisted users to be able to use the router (the normal UX path) will naturally add the router to the allowlist. The bypass is then immediately available to every user. The misconfiguration is not obvious from the extension's interface, which is documented as "Gates `swap` by swapper address, per pool" with no caveat about router identity substitution.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the **economic actor**, not the immediate pool caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check both `sender` and a decoded inner user**: When `sender` is a known periphery contract (router), the extension reads the real user from a standardized field in `extensionData` and checks that address instead.

3. **Document the incompatibility**: If the design intent is that allowlisted pools must be accessed directly (not via router), the extension and pool documentation must state this explicitly, and the router should revert when calling an allowlist-gated pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - bob is NOT allowlisted

Attack:
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — pool sees msg.sender = router
  - _beforeSwap(router, ...) is dispatched to SwapAllowlistExtension
  - Extension checks: allowedSwapper[pool][router] == true  ✓
  - Swap executes for bob — allowlist bypassed

Direct call check (for comparison):
  - bob calls pool.swap(...) directly
  - _beforeSwap(bob, ...) dispatched
  - Extension checks: allowedSwapper[pool][bob] == false  → revert NotAllowedToSwap ✓
```

The bypass is reachable through all four public router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) without any special permissions or setup beyond the natural router-allowlisting configuration. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
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
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
