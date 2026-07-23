### Title
SwapAllowlistExtension Checks the Direct Pool Caller (Router) Instead of the Originating User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating EOA. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, any unprivileged user can bypass the per-user restriction by routing through the same public router.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to the before-swap hook:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct caller) is on the allowlist for the calling pool:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` = pool and `sender` = direct caller of `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The originating EOA is never visible to the extension.

**The trap for pool admins:** To allow allowlisted users to swap via the router (the natural periphery path), the admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of the router — including users who were never individually allowlisted. The per-user restriction is silently voided.

The same pattern applies to `exactInput` and `exactOutput` multi-hop paths, where the router is always the direct caller of each pool hop. [4](#0-3) 

---

### Impact Explanation

**High.** A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely the moment the router is allowlisted. Any unprivileged user can execute swaps on the restricted pool, defeating the curation guarantee. Depending on pool configuration (e.g., favorable oracle-anchored pricing, low spread fees), this allows unauthorized parties to extract value from LP positions that were deposited under the assumption of a restricted trading environment.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to have allowlisted the router — a natural and expected step when setting up a pool that should support the standard periphery flow for allowlisted users. Admins who intend to allow only specific users to trade via the router have no mechanism to do so without also opening the pool to all router callers. The misconfiguration is not obvious from the extension's interface or documentation.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the originating user — not the direct pool caller. Two viable approaches:

1. **Extension-data forwarding**: The router encodes `msg.sender` (the original user) into `extensionData` for each hop, and the extension decodes and checks it. This requires a coordinated encoding convention between the router and extension.

2. **Separate sender field in the hook interface**: The pool could pass both the direct caller and the original initiator (e.g., via a separate `origin` argument), allowing extensions to choose which identity to gate.

Until one of these is implemented, pool admins must be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin: setAllowedToSwap(pool, alice, true)
           setAllowedToSwap(pool, router, true)   ← required for Alice to use the router
3. Bob (never allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Router calls pool.swap(bob, ...)
       pool.msg.sender = router
5. Pool calls extension.beforeSwap(router, ...)
       allowedSwapper[pool][router] == true  → passes
6. Bob's swap executes on the restricted pool.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
