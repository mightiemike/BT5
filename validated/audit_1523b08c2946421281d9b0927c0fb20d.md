### Title
SwapAllowlistExtension gates the router address instead of the actual user, making the allowlist either universally bypassable or incompatible with the router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the actual user. This creates an irreconcilable conflict identical in structure to M-01: the pool admin must either (a) allowlist the router — which grants every user on-chain access and nullifies the allowlist — or (b) not allowlist the router — which silently breaks all router-based swaps for every legitimately allowlisted user.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
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

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap` directly, making the router the `msg.sender` seen by the pool:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, …)   ← msg.sender = router
              → _beforeSwap(msg.sender=router, …)
                   → SwapAllowlistExtension.beforeSwap(sender=router, …)
                        → allowedSwapper[pool][router]  ← checks router, not user
```

Two mutually exclusive outcomes result:

| Admin action | Outcome |
|---|---|
| Allowlist the router | `allowedSwapper[pool][router] = true` → every address on-chain can swap; the per-user allowlist is completely bypassed |
| Do not allowlist the router | Every swap through the router reverts with `NotAllowedToSwap`, even for addresses the admin explicitly allowlisted |

There is no configuration that simultaneously (i) restricts swaps to a specific set of users and (ii) allows those users to use the standard router. The guard is structurally misbound to the intermediary, not the principal.

---

### Impact Explanation

**Direct loss path — allowlist bypass:** A pool admin deploys a private LP pool (e.g., a market-making pool intended only for a single counterparty or a KYC-gated venue). To let their allowlisted users trade via the router, the admin must set `allowedSwapper[pool][router] = true`. At that point any address — including adversarial informed traders — can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool. The LPs suffer adverse selection losses they explicitly configured the extension to prevent. Because the pool is oracle-anchored and the oracle price is public, an informed trader can systematically extract value from the pool whenever the oracle lags the true market price.

**Broken core functionality path:** If the admin does not allowlist the router, every allowlisted user's swap reverts. The pool is effectively unusable through the standard periphery, forcing users to interact with the pool directly and implement their own `metricOmmSwapCallback`, which removes the router's slippage and callback-verification protections.

Both outcomes are fund-impacting: the first causes LP principal loss through unrestricted adverse selection; the second renders the pool's liquidity inaccessible via the intended interface.

---

### Likelihood Explanation

The `SwapAllowlistExtension` is a production-ready, deployed extension explicitly documented for "private or permissioned liquidity pools." Any pool admin who configures it with per-user allowlists and expects users to trade through the router will encounter this conflict on the first swap attempt. The router is the primary and recommended swap interface; direct pool interaction is not documented as the intended path for end users. The conflict is therefore triggered by normal, expected usage.

---

### Recommendation

The extension must identify the actual human/account initiating the swap, not the intermediary contract. Two approaches:

1. **Pass the real initiator through `extensionData`**: The router encodes the original `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: If the pool's invariant is that the recipient of swap output is the party that should be allowlisted (true for `exactInputSingle` where `recipient` is the user), the extension can check `recipient` (the second unnamed parameter in `beforeSwap`). This is already available in the hook signature.

3. **Document the constraint explicitly**: At minimum, document that `allowedSwapper` entries must be router addresses (not user addresses) when router-based access is intended, and that per-user gating requires direct pool interaction.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only intended swapper.
3. Alice calls `MetricOmmSimpleRouter.exactInputSingle({..., recipient: alice})`.
4. Router calls `pool.swap(alice, zeroForOne, amount, priceLimit, callbackData, extensionData)` — `msg.sender` to the pool is the router.
5. Pool calls `_beforeSwap(router, alice, ...)`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` evaluates `allowedSwapper[pool][router]` → `false`.
7. Transaction reverts with `NotAllowedToSwap` even though Alice is explicitly allowlisted.

**To "fix" it**, admin calls `setAllowedToSwap(pool, router, true)`.

8. Now Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
9. `allowedSwapper[pool][router]` → `true` → Bob's swap succeeds.
10. The allowlist is fully bypassed; any user can swap. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
