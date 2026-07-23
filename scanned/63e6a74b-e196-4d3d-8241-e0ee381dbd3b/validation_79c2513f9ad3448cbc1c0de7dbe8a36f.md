### Title
`SwapAllowlistExtension` gates on the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router to enable router-based swaps, every user — including those explicitly excluded from the allowlist — can bypass the guard by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

So when any user calls `router.exactInputSingle(pool, ...)`, the extension sees `sender = router`, not the end user. The pool admin faces an impossible choice:

- **Router not allowlisted**: allowlisted users cannot swap through the router at all (broken core functionality).
- **Router allowlisted**: the check `allowedSwapper[pool][router] == true` passes for every user who routes through the router, regardless of whether that user is individually allowlisted or explicitly excluded.

The second scenario is the direct bypass: a non-allowlisted attacker calls `router.exactInputSingle(pool, ...)` and the extension approves the swap because the router is allowlisted.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted counterparties loses that guarantee entirely once the router is allowlisted. Any unprivileged address can execute live swaps against the pool, receiving real token output and paying real token input, with no allowlist enforcement. This is a direct curation failure on a production extension contract.

---

### Likelihood Explanation

The router is the standard, documented periphery path for EOA swaps. Pool admins who want to support normal user flows must allowlist it. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a call to `router.exactInputSingle` or `router.exactInput`. Any user aware of the router can exploit it immediately after the router is allowlisted.

---

### Recommendation

The extension must gate on the economic actor, not the direct pool caller. Two viable approaches:

1. **Check `recipient` as a proxy for the beneficiary** — but `recipient` is also caller-controlled and may be a third-party address, so this is not reliable.

2. **Require the end user's address in `extensionData`** — the router would encode `msg.sender` into `extensionData`, and the extension would decode and check it. This requires a coordinated change to the router and the extension.

3. **Preferred: gate on `sender` only when `sender` is not a known router; otherwise require the user address in `extensionData`** — the extension can maintain a registry of trusted routers and require them to supply the real user address.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with any intermediary router and must only be used with direct `pool.swap()` calls, or to redesign the extension to accept the real user address via `extensionData` with the router as a trusted forwarder.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, bob, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes and he receives real token output — allowlist fully bypassed.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
