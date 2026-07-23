### Title
SwapAllowlistExtension Checks Router Address as `sender`, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` passed to the extension is the router address — not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user on the network, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol:95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The pool's `msg.sender` is the router, so `sender` delivered to the extension is the router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for pool admins:

| Admin choice | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the network can bypass the allowlist |

The second branch is the bypass: once the router is allowlisted (the only way to enable router-mediated swaps for legitimate users), any disallowed address can call `router.exactInputSingle()` and the extension passes because `allowedSwapper[pool][router] == true`.

The same bypass applies to multi-hop `exactInput` and `exactOutput` paths, and to the recursive `_exactOutputIterateCallback` hops where the router again calls `pool.swap()` as `msg.sender`.

---

### Impact Explanation

A curated pool (KYC-only, institutional, or permissioned) that uses `SwapAllowlistExtension` and also needs to support the official `MetricOmmSimpleRouter` must allowlist the router. Once the router is allowlisted, any unprivileged address can execute swaps on the restricted pool by routing through the router. The attacker receives real token output from the pool at oracle-anchored prices, causing direct loss of LP principal and fee revenue that was reserved for authorized counterparties only.

---

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` to restrict swap access and (b) wants to support the standard periphery router faces this exact conflict. The router is a first-party, publicly documented periphery contract. Pool admins who follow the natural integration path — allowlist the router so their approved users can use it — will unknowingly open the pool to all users. No special privilege, flash loan, or oracle manipulation is required; a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must resolve the actual end-user identity rather than the immediate caller. Two sound approaches:

1. **Pass the original user through the router**: The router stores the original `msg.sender` in transient storage (already done for the payer context). Expose it as a standardized field in `extensionData` and have the extension decode it, verifying that `msg.sender` (the pool's caller) is a known trusted router before trusting the decoded user.

2. **Check `sender` against a router registry and fall back to the decoded user**: If `sender` is a known trusted router, decode the real user from `extensionData`; otherwise treat `sender` as the user directly. This preserves backward compatibility for direct pool calls.

The simplest safe fix is to require that the extension always checks the address that bears the economic consequence of the swap — the address that pays the input token — which the router already tracks in its transient payer slot.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists alice: allowedSwapper[pool][alice] = true
  - Admin allowlists router (to let alice use it): allowedSwapper[pool][router] = true

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., extensionData: ""})
  - Router calls pool.swap() with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  → PASSES
  - Attacker's swap executes; attacker receives token output from the curated pool

Result:
  - allowedSwapper[pool][attacker] == false (never set)
  - But attacker successfully swapped on the restricted pool
  - The allowlist is completely bypassed
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
