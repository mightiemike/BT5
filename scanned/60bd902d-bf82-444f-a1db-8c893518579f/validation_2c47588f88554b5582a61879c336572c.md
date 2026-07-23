### Title
`SwapAllowlistExtension.beforeSwap` gates the caller (`sender`) instead of the economic actor, enabling full allowlist bypass via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces the per-pool allowlist against `sender`, which is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for legitimate users), every unprivileged address can bypass the allowlist by routing through it. `DepositAllowlistExtension.beforeAddLiquidity` handles the analogous deposit guard correctly by checking `owner` (the economic actor), not the caller — the same inconsistency as the external report's `decreaseTraderDebt` / `batchDecreaseTradersDebt` pair.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the caller of the extension). `sender` is whatever `msg.sender` the pool saw when `swap` was called — i.e., the router when the user enters through `MetricOmmSimpleRouter`. [1](#0-0) 

The pool's `swap` function passes `msg.sender` verbatim as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← this is the router, not the end user
    recipient,
    ...
);
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` handles the same concept correctly: it ignores `sender` (first parameter, elided with `address,`) and checks `owner`, the economic actor who will hold the LP position:

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

This is the direct analog of the external report: two parallel guard paths handle the same policy concept (allowlist) but bind to different actors — one correctly to the economic actor, one incorrectly to the caller.

---

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of users has exactly two options:

1. **Do not allowlist the router** → allowlisted users cannot use `MetricOmmSimpleRouter` at all; core swap UX is broken for them.
2. **Allowlist the router** → `allowedSwapper[pool][router] = true`, so every call that arrives through the router passes the check regardless of who the end user is. Any unprivileged address bypasses the allowlist entirely by routing through the public router.

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users. The guard fails open on the most natural production setup. Unauthorized traders can execute swaps on a curated pool, draining LP value through adverse selection or violating the pool's intended access policy.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented in the wiki. A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants their allowlisted users to access the standard router will allowlist the router as a routine configuration step. The bypass is then reachable by any unprivileged address with no special preconditions.

---

### Recommendation

Mirror the `DepositAllowlistExtension` pattern: gate on the economic actor, not the caller. For swaps the economic actor is the end user, not the router. One approach is to have the router forward the originating user address in `extensionData` and have the extension decode it when `sender` is a known router. A simpler approach is to document that the router must never be allowlisted and instead require allowlisted users to call the pool directly — but this breaks the intended UX and is not a code-level fix.

The correct fix is to redesign the `beforeSwap` interface or the router forwarding so the extension always receives the true initiating user, consistent with how `beforeAddLiquidity` already receives `owner`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension`, sets `allowAllSwappers[pool] = false`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the intended curated user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes on the curated pool with no revert, bypassing the allowlist entirely. [1](#0-0) [3](#0-2) [4](#0-3)

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
