### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When any user routes through the public `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's address. A pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users inadvertently opens the pool to every unprivileged caller.

---

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. This creates an inescapable dilemma for pool admins:

- **If the router is not allowlisted**: every allowlisted user who routes through the router is blocked — the allowlist breaks legitimate usage.
- **If the router is allowlisted** (the natural fix): `allowedSwapper[pool][router] = true` is satisfied for every caller of the router, so any unprivileged user bypasses the gate by simply calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`.

The same identity mismatch applies to every multi-hop path in `exactInput` (intermediate hops use `address(this)` as payer) and to the recursive `_exactOutputIterateCallback` path, where the router again calls `pool.swap()` directly: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or protocol-internal actors) loses that restriction entirely once the router is allowlisted. Any unprivileged address can execute swaps at the oracle-derived bid/ask price, draining whichever token the pool holds in excess, extracting LP value, and violating the pool admin's intended access boundary. Because the router is a deployed, immutable, public contract, no on-chain action by the pool admin can prevent this once the router address is in the allowlist.

---

### Likelihood Explanation

- The router is a public, permissionless contract — any EOA or contract can call it.
- Pool admins who want allowlisted users to trade via the router will naturally add the router to the allowlist; the bypass is an automatic consequence.
- No special privilege, flash loan, or oracle manipulation is required; a single `exactInputSingle` call suffices.
- The `SwapAllowlistExtension` provides no mechanism to forward or verify the original `msg.sender` through `extensionData`, so there is no correct workaround within the current design.

---

### Recommendation

The extension must be able to identify the true economic actor, not just the immediate pool caller. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode `msg.sender` (the actual user) into `extensionData` before calling `pool.swap()`, so extensions can read it.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the actual user from `extensionData` when `sender` is a known router/aggregator, and gate on that address instead of (or in addition to) `sender`.

Until both sides are updated, pools that need strict per-user access control must not use `SwapAllowlistExtension` with any router that is itself allowlisted.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool P with SwapAllowlistExtension E.
2. Admin calls E.setAllowedToSwap(P, alice, true)      // Alice is the intended grantee.
3. Admin calls E.setAllowedToSwap(P, router, true)     // Needed so Alice can use the router.

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: P,
           zeroForOne: true,
           amountIn: X,
           recipient: bob,
           ...
       })

5. Router calls P.swap(bob, true, X, ...).
   Pool sets sender = router (msg.sender).
   Pool calls E.beforeSwap(sender=router, ...).
   Extension evaluates: allowedSwapper[P][router] == true  ✓
   Swap executes; Bob receives output tokens.

Result: Bob, who is not allowlisted, completes a swap on a pool
        that was intended to be restricted to Alice only.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
