### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously grants every unprivileged address on-chain the ability to bypass the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all hops including the recursive callback path): [5](#0-4) [6](#0-5) 

The result is a forced dilemma for every pool admin who deploys a restricted pool with `SwapAllowlistExtension`:

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Router-mediated swaps revert for everyone, including legitimate users | Correctly blocked |
| **Allowlist the router** | Router-mediated swaps work | **Bypass: any user calls the router and the check passes** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is intended to restrict swap counterparties — for example, to KYC'd addresses, institutional participants, or protocol-controlled accounts. LP providers deposit into such a pool with the expectation that only vetted counterparties will trade against their liquidity.

Once the router is allowlisted (the only way to make the router usable for legitimate participants), any address can call `MetricOmmSimpleRouter.exactInputSingle()` and execute a swap. The allowlist check passes because it sees the router's address, not the caller's. LP funds are exposed to the full universe of counterparties, defeating the purpose of the restricted pool and exposing LPs to adverse selection, front-running, or other attacks the allowlist was designed to prevent. This constitutes a direct loss of the protection over LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. This is the expected operational configuration, not an edge case. Once that configuration is in place, the bypass is reachable by any unprivileged address with zero preconditions.

---

### Recommendation

The extension must gate the **economic actor**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap()`**: check `recipient` (the address receiving output tokens) in addition to or instead of `sender`, or require the router to forward the originating user's address in `extensionData` and decode it in the hook.

2. **In `MetricOmmSimpleRouter`**: forward `msg.sender` (the end user) inside `extensionData` so that the extension can decode and check the true originator. The extension should then verify the decoded address against the allowlist rather than the raw `sender` argument.

The cleanest fix is option 2: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension` decodes and checks it when `sender` is a known router address, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Deploy pool with ext as beforeSwap extension
address pool = factory.deployPool(..., ext, ...);

// Admin allowlists the router so that legitimate users can use it
ext.setAllowedToSwap(pool, address(router), true);
// Admin does NOT allowlist attacker
// allowedSwapper[pool][attacker] == false

// Attacker (not on allowlist) calls the router
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// ✓ Swap succeeds: beforeSwap checked allowedSwapper[pool][router] == true
// ✓ Attacker swapped on a pool they were explicitly excluded from
```

The `beforeSwap` hook receives `sender = address(router)`, finds it allowlisted, and returns the success selector. The attacker's identity is never checked. [3](#0-2) [7](#0-6) [8](#0-7)

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
