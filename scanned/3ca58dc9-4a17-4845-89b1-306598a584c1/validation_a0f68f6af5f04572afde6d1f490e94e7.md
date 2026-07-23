### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the original user, allowing complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the original user. If a pool admin allowlists the router address to enable router-mediated swaps for their permitted users, every unprivileged address can bypass the allowlist by calling any router entry point.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

**Step 2 — Router is the direct caller of `pool.swap()`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, so the pool sees `msg.sender = router`: [3](#0-2) 

The same is true for `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 3 — Extension checks the router address, not the original user.**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router: [4](#0-3) 

**Step 4 — Admin allowlists the router to enable router-mediated swaps.**

A pool admin who wants allowlisted users to be able to use the standard router has no other option than to call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` and the check on line 37 passes for **every** caller of the router, regardless of whether that caller is on the allowlist.

---

### Impact Explanation

Any address — including addresses the pool admin explicitly excluded — can execute swaps on a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The allowlist guard is completely neutralised. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated trading, rate-limited access), this allows:

- Unrestricted token swaps against pool reserves by non-permitted actors.
- Extraction of value from a pool whose liquidity providers deposited under the assumption that only vetted counterparties could trade against them.

---

### Likelihood Explanation

The router is the canonical periphery contract for end-user swaps. A pool admin who configures `SwapAllowlistExtension` and also wants their allowlisted users to be able to use the router will naturally call `setAllowedToSwap(pool, router, true)`. The admin's intent is to permit the router *for allowlisted users*, but the extension's identity model makes no such distinction — allowlisting the router is equivalent to disabling the allowlist entirely for router-mediated paths. This is a predictable operational mistake with no on-chain safeguard.

---

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two viable approaches:

1. **Encode original user in `extensionData`**: Require the router to encode `msg.sender` (the original caller) into `extensionData`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` as a proxy**: For single-hop swaps the recipient is often the original user, but this is not reliable for multi-hop or third-party recipient flows.

3. **Reject router-mediated swaps entirely**: Document that pools using `SwapAllowlistExtension` must be called directly (not via the router), and add a check that `sender` is an EOA or a known-safe contract.

The cleanest fix is approach 1: the router encodes `msg.sender` into `extensionData` and the extension decodes it, so the allowlist always gates the economic actor rather than the intermediary.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is permitted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted to let alice use it
  - Pool admin does NOT call setAllowedToSwap(pool, eve, true) // eve is NOT permitted

Attack:
  - eve calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) → pool sees msg.sender = router
  - _beforeSwap(router, ...) is dispatched to SwapAllowlistExtension
  - Extension evaluates: allowedSwapper[pool][router] == true  → passes
  - eve's swap executes against pool reserves despite being excluded from the allowlist
``` [4](#0-3) [5](#0-4) [6](#0-5)

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
