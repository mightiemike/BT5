Now I have enough to analyze the full call chain. Let me read the `MetricOmmPoolLiquidityAdder` and `BaseMetricExtension` to complete the picture.

### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the gate to every user, defeating the per-user curation the extension was deployed to enforce.

---

### Finding Description

The call chain is:

1. **User → `MetricOmmSimpleRouter.exactInputSingle()`** — the router is `msg.sender` of the pool call.
2. **Router → `MetricOmmPool.swap(recipient, ...)`** — the pool records `msg.sender` (= router) as `sender`.
3. **Pool → `ExtensionCalling._beforeSwap(msg.sender=router, recipient, ...)`** — `sender` forwarded to the extension is the router address.
4. **Extension → `SwapAllowlistExtension.beforeSwap(sender=router, ...)`** — the guard evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), but `sender` is the router, not the end user. The lookup is `allowedSwapper[pool][router]`.

The pool admin configures the allowlist with individual user addresses via `setAllowedToSwap(pool, alice, true)`. Those entries are keyed on `alice`, but the extension never sees `alice` — it sees the router. The admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot swap through the router at all (their allowlist entries are never matched).
- **Allowlist the router** → `allowedSwapper[pool][router] = true` passes for every user regardless of their individual allowlist status, completely bypassing the curation.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (second parameter), which the pool passes as the position owner supplied by the caller — the liquidity adder correctly forwards the real user as `owner`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, protocol partners, or whitelisted strategies) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The bypassing user executes real swaps against pool liquidity, extracting output tokens and paying input tokens through the router's callback — a complete, fund-impacting trade that the allowlist was supposed to block. LP funds are exposed to trades from actors the pool admin explicitly excluded.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard public entrypoint for swaps in the periphery. Any pool that deploys `SwapAllowlistExtension` and also needs to support router-based swaps (the common case) is immediately vulnerable. The admin has no in-protocol mechanism to allowlist the router while still enforcing per-user restrictions. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle`.

---

### Recommendation

The extension must gate on the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Two approaches:

1. **Check `recipient` instead of `sender`** for the swap allowlist, since `recipient` is the address that receives output tokens and is the actor the pool admin intends to gate. The pool passes `recipient` as the second argument to `beforeSwap`.

2. **Require the router to forward the originating user** — add a `payer`/`originator` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and verify it. This requires router cooperation and is more complex.

Option 1 is simpler and consistent with how `DepositAllowlistExtension` correctly gates on `owner` (the position owner) rather than `sender` (the immediate caller). [5](#0-4) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin calls setAllowedToSwap(pool, router, true)  ← required to enable router usage.

Attack (Bob, not allowlisted):
  1. Bob calls router.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient=Bob, ...) — msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. Bob's swap executes against pool liquidity.
  6. Bob receives output tokens; pool receives Bob's input tokens.

Result: Bob, who is not on the allowlist, successfully swaps on a curated pool.
        Alice's individual allowlist entry is irrelevant when the router is allowlisted.
``` [6](#0-5) [7](#0-6)

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
