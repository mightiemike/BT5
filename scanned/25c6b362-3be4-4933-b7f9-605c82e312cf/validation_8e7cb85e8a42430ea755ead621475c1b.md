The call chain is fully traceable from the code. Here is the complete analysis:

---

## Call Chain Trace

**`MetricOmmPool.swap()`** passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**`ExtensionCalling._beforeSwap()`** forwards that `sender` verbatim to the extension: [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap()`** checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**`MetricOmmSimpleRouter.exactInput()`** calls `pool.swap()` directly — so `msg.sender` seen by the pool is always the **router**, never the original EOA: [4](#0-3) 

---

## The Invariant Break

When a pool admin allowlists a user's EOA address (`allowedSwapper[pool][userEOA] = true`) but does **not** allowlist the router, the check in `beforeSwap` evaluates:

```
!allowAllSwappers[pool] && !allowedSwapper[pool][router]
```

`router` is not allowlisted → `NotAllowedToSwap` revert. The allowlisted user **cannot swap through the router at all** — not just on intermediate hops, but on any hop including single-hop `exactInputSingle`.

This is not limited to multi-hop paths. The question's framing of "intermediate hop" is slightly imprecise, but the core vulnerability is real and applies to every router-mediated swap.

---

## Is This a Design Choice or a Bug?

The existing test in `FullMetricExtension.t.sol` allowlists `callers[0]` (a `TestCaller` contract that calls the pool directly), not the user's EOA: [5](#0-4) 

This confirms the extension gates the **direct caller of `pool.swap()`**, not the originating EOA. There is no mechanism in the extension or the router to thread the original user identity through to the allowlist check. The consequence is:

- Pool admins **cannot** allowlist individual users who use the router
- To allow any router user, admins must allowlist the router address itself — which allows **all** router users, defeating per-user curation
- Allowlisted users who rely on the router have **no functional swap path** on allowlisted pools

This is broken core swap functionality, not a gas issue or a style issue.

---

### Title
Router-Mediated Swaps Always Fail SwapAllowlistExtension Per-User Check — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the check evaluates the router's allowlist status, not the user's. Pool admins cannot allowlist individual users who use the router; allowlisted users cannot swap through the supported router.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. `MetricOmmSimpleRouter.exactInput` and `exactInputSingle` call `pool.swap()` directly, making the router the `msg.sender`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][userEOA]`. No mechanism exists to thread the original caller's identity through the router to the extension.

### Impact Explanation
Allowlisted users who use the router (the standard periphery path) are permanently blocked from swapping on pools with `SwapAllowlistExtension` configured. The only workaround is to call `pool.swap()` directly, bypassing the router entirely. This renders the per-user allowlist feature non-functional for the router path and breaks core swap functionality for the intended beneficiaries of the allowlist.

### Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` with individual user allowlisting and expects users to use the router will exhibit this failure. It is triggered by normal, expected usage of the supported periphery.

### Recommendation
The extension should accept the original initiator identity via a trusted channel. One approach: the router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and checks that address only when the direct caller is a known trusted router. Alternatively, the pool could expose a separate `swapWithOriginator` entry point that passes the original caller explicitly, or the allowlist documentation must clearly state that only direct `pool.swap()` callers can be individually allowlisted.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Call `swapExtension.setAllowedToSwap(pool, userEOA, true)` — do **not** allowlist the router.
3. `vm.prank(userEOA); router.exactInputSingle(...)` → reverts with `NotAllowedToSwap`.
4. `vm.prank(userEOA); pool.swap(...)` directly → succeeds.

The revert on step 3 proves the invariant is broken: an individually allowlisted user cannot trade through the supported router.

### Citations

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
