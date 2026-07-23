### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. That argument is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users reach the pool through the router), every unprivileged user can bypass the individual allowlist by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist, using `msg.sender` (the pool) as the mapping key:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInput` (or any other router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address:

```solidity
// MetricOmmSimpleRouter.sol line 104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
  .swap(
    i == last ? params.recipient : address(this),
    zeroForOne,
    amount,
    MetricOmmSwapPath.openLimit(zeroForOne),
    "",
    params.extensionDatas[i]
  );
```

So the allowlist check becomes `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`. The extension has no visibility into who called the router.

This creates an inescapable dilemma for the pool admin:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | Blocked (unusable) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

Allowlisting the router is the only way to let legitimate allowlisted users reach the pool through the router, but doing so opens the gate to every user of that public router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., trusted market makers, KYC-verified counterparties, or protocol-internal contracts) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter.exactInput` / `exactInputSingle` / `exactOutput` / `exactOutputSingle`. The attacker executes real swaps — with full token settlement through the swap callback — against LP funds that were intended to be protected. This constitutes a direct loss of LP principal through unauthorized toxic flow or arbitrage extraction.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is a natural operational step, not an exotic misconfiguration. Once the router is allowlisted, the bypass is available to every Ethereum address with no further preconditions.

---

### Recommendation

The `sender` field passed to `beforeSwap` must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-level**: Have `MetricOmmSimpleRouter` pass the original `msg.sender` (the actual user) through `extensionData` so that the extension can decode and check the real actor. The extension would need to be updated to read this field when present.

2. **Extension-level**: Document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps unless the pool admin intends to allowlist the router as a single entity (granting access to all router users). Add a NatSpec warning to `beforeSwap` and to the extension's README.

The cleanest long-term fix is for the pool's `swap` function to accept an explicit `payer` / `originator` argument that the router populates with `msg.sender`, and for the extension framework to forward that field separately from the callback-settlement `sender`.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured. Pool admin allowlists Alice (`allowedSwapper[pool][alice] = true`) and the router (`allowedSwapper[pool][router] = true`) so Alice can use the router.

2. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.

3. `_beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap`. The check evaluates `allowedSwapper[pool][router] == true` → passes.

4. Bob's swap executes fully: tokens are transferred out of the pool to Bob's `recipient`, and Bob pays the input leg through the swap callback. The allowlist guard was never applied to Bob's actual address.

5. Bob repeats this for any swap direction, draining LP value through arbitrage or toxic flow that the allowlist was intended to prevent. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
