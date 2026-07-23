### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks the router's address rather than the end user's address. Any user can bypass a per-user swap allowlist on a curated pool by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router contract**, so `sender` delivered to `beforeSwap` is the router address, not the end user. The allowlist lookup `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`.

This produces two broken outcomes:

1. **Allowlist bypass**: If the pool admin allowlists the router address (the natural step to let users trade through the router), every user—including those the admin explicitly excluded—can swap freely by routing through the router.
2. **Allowlisted users blocked from the router**: If the pool admin allowlists individual user addresses but not the router, those users cannot use the router at all, even though they are explicitly permitted.

The same mismatch applies to multi-hop `exactInput` and `exactOutput` paths, where the router calls `pool.swap()` for every hop. [5](#0-4) 

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that guarantee entirely for any user who routes through `MetricOmmSimpleRouter`. The router is a public, permissionless contract. An excluded user needs only call `exactInputSingle` with the curated pool address to execute a swap that the allowlist was supposed to block. This constitutes a direct bypass of a configured access-control guard with fund-impacting consequences: the pool trades with counterparties the admin explicitly rejected, which can drain LP value or violate regulatory/compliance constraints the pool was designed to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing entry point documented and deployed alongside the core pool. Any user aware of the allowlist restriction can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-block setup are required—a single `exactInputSingle` call suffices.

---

### Recommendation

The allowlist must gate the **end user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` forward the original `msg.sender` as an additional field in `extensionData` (or a dedicated parameter), and have `SwapAllowlistExtension` decode and verify that value when `sender` is a known router.

2. **Check `sender` against a router registry and fall back to the original caller**: Alternatively, `SwapAllowlistExtension` can maintain a registry of trusted routers; when `sender` is a trusted router, it reads the actual user from `extensionData` and checks that address instead.

The simplest safe fix is to require that any allowlisted pool be accessed only through a router that forwards the original caller identity in `extensionData`, and have the extension enforce that binding.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin does NOT allowlist bob.

Direct swap (blocked correctly):
  - bob calls pool.swap(...) directly.
  - pool passes msg.sender = bob to _beforeSwap.
  - SwapAllowlistExtension checks allowedSwapper[pool][bob] → false → reverts NotAllowedToSwap. ✓

Router bypass (allowlist defeated):
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(...); msg.sender to pool = router.
  - pool passes msg.sender = router to _beforeSwap.
  - SwapAllowlistExtension checks allowedSwapper[pool][router].
  - If router is allowlisted (admin enabled router for alice): → true → bob's swap succeeds. ✗
  - Even if router is not allowlisted: alice cannot use the router either,
    because allowedSwapper[pool][router] = false for her too. ✗
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
