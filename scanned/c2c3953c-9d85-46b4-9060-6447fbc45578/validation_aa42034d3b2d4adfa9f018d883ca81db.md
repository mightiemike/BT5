### Title
`SwapAllowlistExtension` gates the router address instead of the original user, allowing any trader to bypass a curated pool's swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the original EOA. The allowlist therefore checks the router's address, not the trader's. A pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently opens the pool to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The router stores the original `msg.sender` only in transient storage for the payment callback — it is never forwarded to the pool as the swap initiator. The pool has no mechanism to recover the original EOA from the router call.

The result is a two-sided failure:

| Scenario | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all; they must call `pool.swap()` directly |
| Router **allowlisted** | Every user on the network can bypass the allowlist by routing through the router |

The second scenario is the critical one. A pool admin who wants allowlisted users to be able to use the standard periphery router will allowlist the router address. At that point `allowedSwapper[pool][router] == true`, and the check `allowedSwapper[msg.sender][sender]` passes for every caller regardless of their identity.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled addresses) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The unauthorized user receives pool output tokens and the pool's LP positions absorb the trade at oracle-derived prices, directly impacting LP principal. This is a broken core pool functionality / admin-boundary break with direct loss of LP assets above contest thresholds.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a curated pool and then allowlists the router (the natural step to make the pool usable via the standard periphery) triggers the bypass. The attacker requires no special privilege — only the ability to call a public router function. The bypass is reachable on every swap path (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

---

### Recommendation

The `sender` forwarded to extensions should represent the **original initiating user**, not the direct caller of `pool.swap()`. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` as an additional field in `callbackData` or `extensionData` so extensions can recover it. Alternatively, add a dedicated `originSender` parameter to the pool's swap interface.

2. **In `SwapAllowlistExtension`**: decode the original sender from `extensionData` when the direct caller is a known router, or require the pool to be called directly (no router intermediary) for allowlisted pools.

The `DepositAllowlistExtension` correctly avoids this problem by checking `owner` (the position owner, explicitly passed by the caller) rather than `sender` (the direct caller). The swap allowlist should adopt the same pattern — gate the economically relevant actor, not the intermediary.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  pool admin does NOT allowlist attacker EOA

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool sets sender = address(router)
    → _beforeSwap(address(router), ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → passes
    → swap executes, attacker receives output tokens

Result:
  attacker (not in allowlist) successfully swaps on a curated pool.
  allowedSwapper[pool][attacker] == false was never checked.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```
