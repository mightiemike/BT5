### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool hardcodes to `msg.sender` (i.e., whoever called `pool.swap()`). When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This wrong-actor binding either silently bypasses the per-user allowlist (if the router is allowlisted) or permanently blocks allowlisted users from using the router (if it is not).

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` argument against the per-pool allowlist:

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

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first argument — the router's address when the user routes through `MetricOmmSimpleRouter`. The actual end user's address is never visible to the extension.

**Scenario A — Allowlist bypass (higher impact):**
A pool admin configures `SwapAllowlistExtension` to restrict swaps to a curated set of users. To support router-mediated swaps, the admin also allowlists the router address (`allowedSwapper[pool][router] = true`). Because the extension sees `sender = router` for every router-mediated swap, any user — including non-allowlisted ones — can now swap freely by routing through `MetricOmmSimpleRouter`. The per-user allowlist is completely bypassed.

**Scenario B — Broken functionality (guaranteed):**
If the admin does not allowlist the router, every allowlisted user who calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) receives `NotAllowedToSwap`, even though they are individually allowlisted. The only working path is a direct `pool.swap()` call, which is not the intended UX for most users.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (explicitly passed by the caller), not `sender`. The asymmetry between the two extensions confirms this is a design defect specific to `SwapAllowlistExtension`.

---

### Impact Explanation

**Scenario A:** Non-allowlisted users gain full swap access to a curated pool by routing through the public `MetricOmmSimpleRouter`. The pool admin's curation policy is nullified. Any value-extraction or front-running that the allowlist was designed to prevent becomes possible for any address.

**Scenario B:** Allowlisted users are permanently locked out of the router path. Their only recourse is a raw `pool.swap()` call, which most wallets and integrations do not support. This is broken core swap functionality causing an unusable swap flow for legitimate users.

Both impacts are direct and fund-relevant: Scenario A enables unauthorized trading on pools with restricted liquidity; Scenario B prevents authorized users from executing swaps.

---

### Likelihood Explanation

**Scenario A:** Requires the pool admin to allowlist the router address. This is a natural and expected operational step — any pool that wants to support the standard periphery UX must allowlist the router. The likelihood is therefore high whenever `SwapAllowlistExtension` is used alongside `MetricOmmSimpleRouter`.

**Scenario B:** Requires only that an allowlisted user attempts to use the router, which is the default UX path. This is guaranteed to occur in normal operation.

---

### Recommendation

The extension must check the actual end user, not the intermediate router. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward the original `msg.sender` as part of `extensionData`, and `SwapAllowlistExtension` should decode and check that address when `sender` is a known router.

2. **Preferred — check `recipient` or a dedicated user field**: Redesign `SwapAllowlistExtension` to gate the `recipient` (the address receiving output tokens) rather than `sender`, since the recipient is the economically relevant actor and is always the end user regardless of routing path. Alternatively, add a `trustedRouter` registry so the extension can unwrap the real caller when `sender` is a known router.

3. **Immediate mitigation**: Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and must only be used with direct `pool.swap()` calls until the actor-binding is corrected.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address
  - Pool admin DOES allowlist alice address

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=attacker, ...)
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  → passes
  - Attacker's swap executes successfully despite not being allowlisted

Verification:
  - attacker calls pool.swap() directly
  - Pool calls extension.beforeSwap(sender=attacker, ...)
  - Extension checks: allowedSwapper[pool][attacker] == false → reverts NotAllowedToSwap
  - Direct call correctly blocked, but router path is open to all
```

The two code paths produce opposite results for the same attacker, confirming the wrong-actor binding is the root cause. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
