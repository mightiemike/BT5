### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the actual user. If the pool admin allowlists the router to enable periphery usage, every unpermissioned user can bypass the curated pool's swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that value and calls each extension in order:

```solidity
// ExtensionCalling.sol
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When a user goes through `MetricOmmSimpleRouter.exactInputSingle()`:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router calls `pool.swap(...)` directly, so `pool.msg.sender = router_address`. The extension therefore evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`.

**Two exploitable outcomes:**

| Scenario | Condition | Effect |
|---|---|---|
| **Allowlist bypass** | Admin allowlists the router to enable periphery usage | Every user, including explicitly blocked ones, can swap by routing through the router |
| **Broken functionality** | Router is not allowlisted | Allowlisted users cannot use the router; they must call the pool directly |

The bypass scenario is the critical one. A pool admin who wants to support both curated access and router convenience will naturally add the router to the allowlist. Once the router is allowlisted, the curation is entirely defeated.

---

### Impact Explanation

Any user can trade on a curated pool that was designed to restrict access to specific addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). The allowlist provides zero protection against router-mediated swaps. Depending on the pool's purpose, this can result in:

- Direct loss of LP principal if the pool was designed to only accept trades from trusted counterparties at oracle-anchored prices
- Protocol-level curation failure: the pool's access control invariant is broken for every swap that enters through the router

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and deployed by the protocol
- Pool admins who configure `SwapAllowlistExtension` and also want to support router usage will allowlist the router — this is the natural operational path
- No special privileges or malicious setup are required; any user can call `exactInputSingle` or `exactInput` on the router
- The bypass is unconditional once the router is allowlisted: no timing, no oracle manipulation, no flash loan needed

---

### Recommendation

Pass the **original transaction initiator** (`tx.origin`) or, preferably, thread the actual user address through the router → pool → extension call chain. The cleanest fix is to have the router encode the real payer/user in `extensionData` and have the extension decode it, or to add a dedicated `swapFor(address onBehalfOf, ...)` entrypoint on the pool that the router calls with the real user address as `sender`.

Alternatively, the extension can be redesigned to check `recipient` instead of `sender` when the pool is called via a known router, but this requires a trusted router registry and is more complex.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router_address, true)` to enable router usage.
3. Pool admin does **not** allowlist `attacker_address`.
4. Attacker calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ..., extensionData: ""})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Extension evaluates `allowedSwapper[pool][router_address]` → `true` → swap proceeds.
7. Attacker receives tokens from the curated pool despite never being allowlisted.

The same path works for `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
