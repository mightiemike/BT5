### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual user. The allowlist therefore gates the router address, not the individual swapper. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user — including those explicitly excluded — can bypass the curated pool's access control.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   [msg.sender = router]
              → MetricOmmPool._beforeSwap(msg.sender=router, recipient, ...)
                   → ExtensionCalling._callExtensionsInOrder
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← router address, not the actual user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The dilemma this creates for pool admins:**

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user (including blocked ones) can swap via the router — allowlist is fully bypassed |
| No | Even individually allowlisted users cannot use the router — router-mediated swaps are broken for everyone |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners). The admin allowlists the router so that legitimate users can access the pool through the standard periphery. Any non-allowlisted user then calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the curated pool. The extension sees `sender = router`, which is allowlisted, and the swap proceeds. The curation policy is completely defeated. The pool trades at oracle-anchored prices, so the attacker receives real token output at the configured bid/ask — direct loss of the pool's intended access restriction with fund-impacting consequences (unauthorized parties drain liquidity at oracle prices).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Any user who knows the pool address can call it. The bypass requires no special privileges, no flash loans, and no price manipulation — only a standard router call. The only precondition is that the router is allowlisted, which is the natural configuration any pool admin would choose to make the pool usable through the official periphery.

---

### Recommendation

The allowlist must gate the **economically relevant actor** — the end user — not the intermediate router. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` (the actual user) as a dedicated `swapper` field in `extensionData`, and have `SwapAllowlistExtension` decode and check that field instead of the `sender` argument.

2. **Alternatively, in `SwapAllowlistExtension.beforeSwap`**: accept an optional `swapper` override from `extensionData`; if present, check that address; otherwise fall back to `sender`. This preserves backward compatibility for direct pool calls while enabling correct gating through the router.

The core invariant to enforce: the address checked against `allowedSwapper` must be the address that initiated the transaction and controls the economic action, not any intermediate forwarder.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   (admin enables router-mediated swaps)
  allowedSwapper[pool][attacker] = false (attacker is explicitly blocked)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: X,
    ...
  })

  → router calls pool.swap(attacker, true, X, ...)  [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  → swap executes; attacker receives token1 output at oracle price
  → allowlist policy is bypassed with zero special access
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
