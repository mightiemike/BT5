### Title
SwapAllowlistExtension Gates Router Address Instead of Economic Actor, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is `msg.sender` of the pool's `swap()` call — against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the end user's address. If a pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user restriction by routing through the router.

---

### Finding Description

**Step 1 — What the allowlist checks.**

`SwapAllowlistExtension.beforeSwap` gates on `sender` (the first argument) keyed by `msg.sender` (the pool): [1](#0-0) 

`msg.sender` inside the extension is the pool; `sender` is whatever the pool passed as the first argument to the hook.

**Step 2 — What the pool passes as `sender`.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, so `sender` = the immediate caller of the pool's `swap()`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this verbatim: [3](#0-2) 

**Step 3 — What the router passes as the pool's `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The pool's `msg.sender` is therefore the **router address**, not the end user: [4](#0-3) 

**Step 4 — The identity mismatch.**

The allowlist is keyed `allowedSwapper[pool][sender]`. When a user swaps via the router:

- `sender` = router address
- The check becomes `allowedSwapper[pool][router]`
- The actual user's address is never consulted

If the pool admin allowlists the router (a natural step to enable router-mediated swaps for their permitted users), the check passes for **every** user who routes through the router, regardless of whether that user is individually permitted.

---

### Impact Explanation

The `SwapAllowlistExtension` is the only on-chain mechanism to restrict which addresses may swap on a given pool. Pools that deploy this extension are typically designed to limit swaps to trusted counterparties (e.g., specific market makers) to prevent retail users from executing swaps at oracle prices that are temporarily unfavorable to LPs. If the router is allowlisted, any unprivileged user can bypass this restriction, execute swaps at those oracle prices, and extract value from LP positions — a direct loss of LP principal.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a plausible and natural configuration: an admin who wants their permitted users to interact via the standard periphery router would add the router to the allowlist, not realizing that doing so opens the gate to all router users. The admin's intent (allow router-mediated swaps for specific users) and the actual effect (allow all router users) are silently mismatched. No malicious admin assumption is required; the misconfiguration follows from a reasonable but incorrect mental model of how the extension works.

---

### Recommendation

1. **Check the economic actor, not the entry point.** The extension should gate the address that controls the swap economically. One approach: require the router to forward the originating user's address in `extensionData`, and have the allowlist decode and check that address.
2. **Alternatively**, document explicitly that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that any pool using the allowlist must require direct pool calls only.
3. **Consider a two-layer check**: allowlist both the entry point (router vs. direct) and the originating user, so the extension can distinguish "router call from allowlisted user" from "router call from arbitrary user."

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin allowlists the router:
       extension.setAllowedToSwap(pool, address(router), true);
   (Admin intent: allow their permitted users to use the router.)

3. Unauthorized user (not individually allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           recipient: attacker,
           ...
       });

4. Router calls pool.swap(attacker, ...).
   Pool's msg.sender = router.
   Pool calls _beforeSwap(router, attacker, ...).
   Extension checks: allowedSwapper[pool][router] → true.
   Swap proceeds.

5. Attacker has bypassed the per-user allowlist and executed a swap
   on a pool designed to be restricted, potentially draining LP funds
   at unfavorable oracle prices.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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
