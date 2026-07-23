### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end user. The extension therefore checks the router's allowlist status rather than the real swapper's identity. If the router is allowlisted to permit legitimate users, the allowlist is fully bypassed for every user; if the router is not allowlisted, every allowlisted user is broken from using the standard periphery path.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct), and `sender` is whatever the pool passes as the caller identity into `_beforeSwap`. The pool passes `msg.sender` of the `swap` call as `sender` to `ExtensionCalling._beforeSwap`: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutput`, `exactOutputSingle`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract:

```solidity
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
``` [3](#0-2) 

The router stores the real user in the transient callback context (for payment), but the `sender` forwarded to the pool's `swap` call — and therefore to the extension — is the router's address, not the original `msg.sender`. The allowlist check therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This is structurally identical to the Magnetar analog: a required identity-forwarding step (passing the real user as `sender`) is effectively skipped by the router, and the downstream guard (the allowlist check) assumes the correct actor is present but receives the wrong one.

---

### Impact Explanation

Two mutually exclusive failure modes, both fund-impacting:

**Mode A — Allowlist bypass (High):** The pool admin allowlists the router address (the only way to let legitimate users swap through the standard periphery path). Any unprivileged user can then call `MetricOmmSimpleRouter.exactInputSingle` and the extension sees `sender = router`, which is allowlisted. The curated pool's access control is completely defeated. Unauthorized users trade on pools intended for KYC'd, institutional, or otherwise restricted participants.

**Mode B — Broken core swap path (High):** The pool admin does not allowlist the router (correctly intending to gate individual users). Every allowlisted user who calls through the router receives `NotAllowedToSwap`. The standard periphery swap path is unusable for the pool, breaking core swap functionality for all legitimate users.

---

### Likelihood Explanation

Likelihood is high. `MetricOmmSimpleRouter` is the documented standard swap entrypoint for EOAs. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will immediately encounter one of the two failure modes. No special timing, privileged access, or exotic token behavior is required — a single `exactInputSingle` call from any address reproduces the issue.

---

### Recommendation

The pool must forward the original caller's identity through the router so the extension can check the real user. Two complementary fixes:

1. **Router-side:** Pass the original `msg.sender` as an explicit `sender` argument to `pool.swap`, or encode it in `extensionData` for the extension to decode.

2. **Extension-side:** `SwapAllowlistExtension.beforeSwap` should accept and decode a trusted `sender` from `extensionData` when the direct `sender` argument is a known router, or the pool interface should be extended to carry the originating EOA through the call stack.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner explicitly passed by the caller) rather than `sender`: [4](#0-3) 

The swap allowlist should adopt the same pattern — gate on the economically relevant actor (the user whose funds are being spent), not the intermediary contract that relays the call.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured. Set `allowAllSwappers[pool] = false`. Call `setAllowedToSwap(pool, alice, true)` to allowlist only Alice.

2. **Mode A (bypass):** Call `setAllowedToSwap(pool, router, true)` to allow the router (required for any router-mediated swap). Now Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` with `pool` as target. The pool calls `_beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router] == true` → swap succeeds. Bob bypasses the allowlist.

3. **Mode B (broken path):** Do not allowlist the router. Alice (allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle`. The pool calls `_beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router] == false` → `NotAllowedToSwap` revert. Alice cannot use the standard periphery path despite being explicitly allowlisted. [5](#0-4) [6](#0-5) [2](#0-1)

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
