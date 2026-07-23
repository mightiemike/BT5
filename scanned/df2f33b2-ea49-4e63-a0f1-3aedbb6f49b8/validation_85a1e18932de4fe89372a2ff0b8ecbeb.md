### Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. If the pool admin allowlists the router address (a natural action to enable periphery access for their curated pool), every unprivileged user can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim as the first argument to the extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)   // sender = router address
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The effective check is:

```
allowedSwapper[pool][router]
```

**Not** `allowedSwapper[pool][end_user]`.

A pool admin who wants their allowlisted users to be able to use the official periphery will allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller who routes through the router, regardless of whether that caller is on the per-user allowlist.

Contrast with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly ignores `sender` and checks `owner` (the position owner):

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, ...)  // sender ignored
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The swap extension has no equivalent "check the economically relevant actor" logic.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise vetted addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user receives the full swap output from the pool's LP reserves. LP providers deposited into a curated pool under the assumption that only vetted counterparties would trade against them; the bypass violates that invariant and exposes LP capital to unrestricted counterparty flow.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router — a natural, expected action for any pool that wants its curated users to access the official periphery. The admin has no on-chain signal that doing so opens the allowlist to everyone. The bypass is then reachable by any unprivileged user with zero additional preconditions: call `MetricOmmSimpleRouter.exactInputSingle` pointing at the curated pool.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **end user**, not the direct pool caller. Two options:

1. **Check `recipient` instead of `sender`** — the recipient is the economic beneficiary of the swap and is set by the end user even when routing through the router.
2. **Require the router to forward user identity** — add a `swapper` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and check that field (with the pool still enforcing that `msg.sender` is a registered pool).

Option 1 is simpler and consistent with how `DepositAllowlistExtension` uses `owner` rather than `sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowAllSwappers[pool] = false
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = true  // admin allowlists router so alice can use periphery

Attack:
  bob (not on allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})

  Router calls pool.swap(bob, ...) → msg.sender of pool.swap = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap(sender=router, ...)
    check: allowedSwapper[pool][router] == true  → PASSES
  Bob receives swap output from the curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
