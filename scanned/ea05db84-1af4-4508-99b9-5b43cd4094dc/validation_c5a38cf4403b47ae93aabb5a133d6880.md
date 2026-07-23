Looking at the `SwapAllowlistExtension` and how the router passes the swap call to the pool, there is a clear wrong-actor binding bug.

---

### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` (the router). When a pool admin adds the router to the allowlist so that allowed users can reach the pool through the supported periphery path, every unpermissioned user can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the only valid caller via `onlyPool`). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
```

The pool populates `sender` with its own `msg.sender` — the address that called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router is that caller:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowed users to reach the pool through the router must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every caller — any unpermissioned user can swap by routing through `MetricOmmSimpleRouter`, completely defeating the curation policy.

The same actor-mismatch applies to multi-hop `exactInput` (all hops use the router as `msg.sender`) and `exactOutput` (the recursive callback also calls `pool.swap()` from the router).

`DepositAllowlistExtension` does not share this flaw because it gates `owner` (the position recipient), which the liquidity adder passes explicitly and which the pool records as the LP holder.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that protection entirely once the router is allowlisted. Any unpermissioned address can execute swaps, drain favorable oracle-priced liquidity, or interact with a pool whose terms were never meant to apply to them. This is a direct loss of LP principal and a broken core pool invariant (the allowlist guard fails open on the supported periphery path).

### Likelihood Explanation

Adding the router to the allowlist is the only way to let permitted users reach the pool through the standard periphery. Any operator who deploys a curated pool and also wants router support will make this configuration, making the bypass reachable by any public user with no special privilege.

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Pass the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. The pool admin must trust that the router populates this field honestly (acceptable because the router is a known, audited contract).
2. **Dedicated `sender` forwarding field**: Add an explicit `originalSender` field to the pool's `swap` call that the router populates with `msg.sender`, and forward it to extensions separately from the pool-level `msg.sender`.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; set allowAllSwappers[pool] = false.
2. Admin calls setAllowedToSwap(pool, alice, true)   // Alice is KYC'd
3. Admin calls setAllowedToSwap(pool, router, true)  // needed so Alice can use the router
4. Bob (not KYC'd) calls router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
   → pool.msg.sender = router
6. Pool calls extension.beforeSwap(router, bob, ...)
   → allowedSwapper[pool][router] == true  → no revert
7. Bob's swap executes in the curated pool; allowlist is bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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
