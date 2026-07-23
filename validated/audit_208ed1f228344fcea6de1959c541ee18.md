### Title
`SwapAllowlistExtension` gates the router address instead of the economic actor, allowing any user to bypass per-user swap restrictions when the router is allowlisted — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the user. If the pool admin allowlists the router address to enable router-mediated swaps for their curated users, every unprivileged address can bypass the per-user allowlist by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput` and `exactOutput`. In every router-mediated path the pool sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants their allowlisted users to be able to swap through the standard periphery must therefore add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including addresses the admin never intended to permit. The extension has no way to recover the originating EOA from the `sender` argument alone.

---

### Impact Explanation

The swap allowlist is the primary admin-configured access-control boundary for curated pools (KYC pools, institutional pools, beta-access pools). When the router is allowlisted — the only way to let legitimate users trade through the standard periphery — the boundary collapses entirely for router-mediated paths. Any unprivileged address can trade on the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`). This is an admin-boundary break: the pool admin's configured per-user gate is bypassed by an unprivileged path through a supported periphery contract.

---

### Likelihood Explanation

Medium. The precondition — the router being allowlisted — is the natural, expected configuration for any curated pool whose legitimate users are expected to interact through the standard periphery. A pool admin who allowlists individual users but not the router will find that those users cannot use the router at all, creating strong pressure to allowlist the router. The bypass is therefore reachable on any production curated pool that supports router-mediated swaps.

---

### Recommendation

The extension must gate the economic actor, not the immediate caller of `pool.swap`. Two concrete options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated change to the router and the extension.
2. **Check `sender` and fall back to a router-aware lookup**: The extension maintains a registry of approved routers; when `sender` is a known router, it reads the originating user from a router-provided field in `extensionData` and checks that address against the allowlist instead.

The `DepositAllowlistExtension` avoids this specific problem by checking `owner` (the position owner) rather than `sender`, but introduces a symmetric issue in the opposite direction — anyone can call `addLiquidity` on behalf of an allowlisted owner, depositing tokens into the pool without being allowlisted themselves. [5](#0-4) 

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is the intended user.
3. Pool admin calls setAllowedToSwap(pool, router, true)  // Required so Alice can use the router.
4. Eve (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      <restricted pool>,
           tokenIn:   token0,
           recipient: eve,
           ...
       })
5. Router calls pool.swap(eve, ...) — pool's msg.sender = router.
6. Pool calls _beforeSwap(router, eve, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes.
8. Eve's swap executes on the restricted pool.
   allowedSwapper[pool][eve] was never set; the check never ran against Eve's address.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
