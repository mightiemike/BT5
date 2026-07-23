Now I have enough to write the finding. Let me read the `BaseMetricExtension` and verify the exact flow one more time.

### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any Unprivileged Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender = router`. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. If the router is allowlisted (the only way to permit router-mediated swaps), every user on the network can bypass the individual allowlist by calling through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → ExtensionCalling._callExtensionsInOrder(BEFORE_SWAP_ORDER, ...)
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, `msg.sender` is forwarded verbatim as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address when called through router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` passes it unchanged to the extension:

```solidity
// ExtensionCalling.sol:162-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData))
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The guard resolves to `allowedSwapper[pool][router]`.

**Two broken states result:**

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user bypasses the individual allowlist by routing through the router |
| No | Even individually allowlisted users cannot swap through the router |

There is no configuration that simultaneously allows specific users to swap through the router while blocking others. The allowlist invariant is structurally unenforceable on the router path.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, protocol-owned bots, or whitelisted market makers) cannot enforce that restriction when `MetricOmmSimpleRouter` is in use. Any unprivileged user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` through the router and the extension will check the router's address, not theirs. If the router is allowlisted (the only way to make router swaps work at all), the allowlist is fully bypassed for all users. This breaks the admin-boundary invariant: an unprivileged path defeats a pool-admin-configured access control, and any swap the admin intended to block can be executed freely.

---

### Likelihood Explanation

The bypass requires only that the attacker call through `MetricOmmSimpleRouter` rather than calling `pool.swap` directly. No special privileges, flash loans, or oracle manipulation are needed. The router is a public, permissionless contract. Any user who is individually blocked by the allowlist can trivially route around it. The likelihood is high whenever a pool is configured with `SwapAllowlistExtension` and the router is allowlisted (or the admin has not yet realized the router must be allowlisted for normal usage to work).

---

### Recommendation

The extension must check the economically relevant identity — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`, and the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is often the correct identity to gate. The `recipient` parameter is already forwarded to `beforeSwap` and is set by the user, not the router.

3. **Preferred — add a `realSender` field to the extension interface**: The pool could forward both `msg.sender` (the direct caller) and an optional `realSender` decoded from `extensionData`, letting extensions choose which identity to gate.

At minimum, the `SwapAllowlistExtension` NatDoc and admin tooling must warn that allowlisting the router grants unrestricted swap access to all users.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` as `EXTENSION_1`, `beforeSwap` order = `1`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — necessary for any router swap to succeed.
3. `blockedUser` (not individually allowlisted) calls `router.exactInputSingle(...)`.
4. Pool calls `extension.beforeSwap(sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. `blockedUser`'s swap executes despite never being individually allowlisted.

Concretely, the existing test `FullMetricExtension.t.sol::test_blocksSwapWhenSwapperNotAllowed` only tests direct `pool.swap` calls (via `TestCaller`). It does not test the router path. Adding a router-mediated call from a non-allowlisted user against the same pool configuration would demonstrate the bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
