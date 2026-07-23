The call chain confirms a real actor identity mismatch. Here is the complete trace and verdict:

---

### Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` Per-User Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end user. If the router address is allowlisted, every user can bypass the per-user allowlist by routing through it.

### Finding Description

**Call chain — direct swap (correct):**

```
User → MetricOmmPool.swap()
  pool: _beforeSwap(msg.sender = User, ...)
  extension: beforeSwap(sender = User, ...)
  check: allowedSwapper[pool][User]   ← correct actor
```

**Call chain — router swap (broken):**

```
User → MetricOmmSimpleRouter.exactInputSingle()
  router → IMetricOmmPoolActions(pool).swap(recipient, ...)
  pool: _beforeSwap(msg.sender = Router, ...)
  extension: beforeSwap(sender = Router, ...)
  check: allowedSwapper[pool][Router]  ← wrong actor
```

The pool passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as `sender` to the extension: [2](#0-1) 

The extension then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

The router calls `pool.swap()` directly, so `sender` = router address: [4](#0-3) 

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of addresses (KYC'd users, institutional counterparties, etc.) and also wants those users to be able to use the official router faces an impossible configuration:

- **Don't allowlist the router** → all router-mediated swaps revert with `NotAllowedToSwap`; the router is unusable for the pool.
- **Allowlist the router** → `allowedSwapper[pool][router] = true`, so `beforeSwap` passes for **any** caller who routes through the router, completely defeating the per-user gate.

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users. The allowlist extension's core invariant — "only approved addresses may swap" — is broken for any pool that permits router access.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public entrypoint for swaps. Any pool that deploys `SwapAllowlistExtension` and also allowlists the router (a natural and expected configuration) is immediately exploitable by any address. No privileged access, no oracle manipulation, and no multi-transaction state manipulation is required — a single `exactInputSingle` call suffices.

### Recommendation

The extension must resolve the true end-user identity rather than trusting the `sender` argument when the caller is a known intermediary. Two sound approaches:

1. **Pass the real payer through `extensionData`**: The router already stores the real payer in transient storage (`_getPayer()`). It can encode the real user address in `extensionData`, and the extension can verify it (with a signature or by trusting only the factory-registered router).
2. **Check `recipient` instead of `sender`**: For allowlist purposes, gate on `recipient` (the address receiving tokens) rather than `sender` (the address initiating the call). This is semantically different but may match the pool designer's intent.
3. **Disallow router-mediated swaps entirely for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router and must require direct `pool.swap()` calls.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][Alice] = true      // Alice is approved
  allowedSwapper[pool][Router] = true     // Router allowlisted so Alice can use it

Attack:
  Bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: Bob, ...})

  Pool receives: swap(msg.sender = Router)
  Extension checks: allowedSwapper[pool][Router] → true
  Bob's swap succeeds despite not being on the allowlist.
```

The "two-transaction precursor" framing in the question is not necessary — the bypass works in a single transaction. The core flaw is the structural actor identity mismatch between the address the pool admin intended to gate and the address the hook actually checks.

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
