### Title
`SwapAllowlistExtension.beforeSwap` checks `sender` (the router address) instead of the actual user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is the production extension that gates swaps on curated pools to a per-pool allowlist of swapper addresses. Its `beforeSwap` hook receives two actor parameters — `sender` (the direct `msg.sender` of `pool.swap()`) and `recipient` (the output-token destination). The extension gates on `sender`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the user. A pool admin who allowlists the router (the natural step to enable router-based swaps) inadvertently opens the pool to every user, defeating the per-user restriction entirely.

---

### Finding Description

**Actor binding in the pool → extension call chain**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards those two actors verbatim to the extension: [2](#0-1) 

So in `IMetricOmmExtensions.beforeSwap(address sender, address recipient, ...)`:
- `sender` = `msg.sender` of `pool.swap()` — the **router** when the user goes through `MetricOmmSimpleRouter`
- `recipient` = the address that receives output tokens

**What the extension actually checks**

`SwapAllowlistExtension.beforeSwap` gates on `sender`: [3](#0-2) 

**What the router passes as `sender`**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — so `msg.sender` seen by the pool is the **router contract**, not the end user: [4](#0-3) 

The same holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — every router entry point calls `pool.swap()` with itself as `msg.sender`. [5](#0-4) 

**The bypass**

A pool admin who wants to allow router-based swaps for allowlisted users calls:
```
swapExtension.setAllowedToSwap(pool, address(router), true);
```
This is the only way to make the router work with the extension. But it allowlists the router address globally — so `allowedSwapper[pool][router] == true` passes the check for **every** user who calls through the router, regardless of whether that user is individually permitted. The per-user curation is completely nullified.

Conversely, if the admin allowlists only individual user addresses (not the router), those users cannot swap through the supported periphery at all, breaking core pool functionality.

The actual user's address is never forwarded to the extension hook — it exists only as `msg.sender` inside the router, which is not part of the `beforeSwap` ABI.

**Contrast with `DepositAllowlistExtension`**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the second parameter), which is the position owner passed explicitly by the caller and forwarded unchanged through `MetricOmmPoolLiquidityAdder`: [6](#0-5) 

The deposit path works correctly because `owner` is an explicit argument that the periphery sets to the real user. The swap path has no equivalent explicit user argument — only `sender` (the direct caller) and `recipient` (the output destination).

---

### Impact Explanation

**Direct loss of curation / allowlist bypass.** Any user can trade on a pool that the admin intended to restrict to a curated set of swappers, simply by routing through `MetricOmmSimpleRouter`. The pool's liquidity is exposed to unrestricted swap flow, which may include adversarial actors the admin explicitly excluded. This is a broken core pool functionality / admin-boundary break with direct fund-impacting consequences for LP principals and protocol fees on curated pools.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary supported swap interface. Any user can call it permissionlessly. A pool admin enabling router-based swaps (the expected production configuration) will naturally allowlist the router, triggering the bypass for all users. No special privileges or malicious setup are required.

---

### Recommendation

The `beforeSwap` hook signature does not carry the real end-user address when an intermediary router is used. Two complementary fixes are possible:

1. **Encode the real user in `extensionData`**: Have the router encode `msg.sender` (the real user) into `extensionData` and have `SwapAllowlistExtension` decode and gate on that value. This requires a convention between the router and the extension.

2. **Gate on `recipient` as a proxy for the user**: For single-hop swaps where `recipient` is set to the user's own address, checking `recipient` instead of `sender` would correctly identify the user. This is imperfect for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is option 1: the router should always prepend the real initiating user's address to `extensionData`, and the extension should decode and verify it, falling back to `sender` only when no user address is encoded (direct pool calls).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted, but routes through the router
// pool.swap() sees msg.sender = router → allowedSwapper[pool][router] = true → passes
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: address(token0),
    deadline: block.timestamp,
    extensionData: ""
}));
// bob's swap succeeds despite not being in the per-user allowlist
```

The check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router] == true`, so the guard passes for `bob` even though `allowedSwapper[pool][bob] == false`. [3](#0-2) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
