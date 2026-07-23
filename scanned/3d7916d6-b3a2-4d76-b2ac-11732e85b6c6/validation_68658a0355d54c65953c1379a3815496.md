### Title
`SwapAllowlistExtension` Gates Router Address Instead of Original Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender` and forwards that address as `sender` to the extension hook. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist entirely by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` (and `simulateSwapAndRevert`) calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
_beforeSwap(
  msg.sender,   // ← always the immediate caller of the pool
  recipient,
  ...
)
```

`_beforeSwap` forwards this value as the `sender` parameter to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct), but `sender` is whoever called the pool — the **router**, not the original user, when the swap enters through `MetricOmmSimpleRouter`.

This creates a forced dilemma for any pool that uses `SwapAllowlistExtension` and also wants to support the router:

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot swap through the router at all — broken core functionality |
| Router **allowlisted** | **Any** user can bypass the allowlist by routing through the router |

The second case is the fund-impacting path: a pool designed to restrict swaps to KYC'd, institutional, or otherwise curated addresses is fully open to any public user who calls `MetricOmmSimpleRouter`.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to enforce access control (e.g., regulatory compliance, curated LP pools, institutional-only venues) is rendered completely ineffective. Any unprivileged user can execute swaps against the pool's liquidity by routing through the public `MetricOmmSimpleRouter`. This constitutes a broken core pool functionality with direct fund-impacting consequences: disallowed users can drain or trade against restricted liquidity.

---

### Likelihood Explanation

**High.** The attack requires no special preconditions, no privileged access, and no rare on-chain state. Any user who knows the pool uses `SwapAllowlistExtension` and that the router is allowlisted (or can test it) can immediately exploit this by calling `MetricOmmSimpleRouter` instead of the pool directly. The router is a standard, publicly deployed periphery contract.

---

### Recommendation

The extension must gate the **original user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original user through the call chain.** The pool's `swap` function should accept an explicit `sender` parameter (the original user, set by the router to `msg.sender` before calling the pool), and forward that to the extension hook instead of `msg.sender`.

2. **Alternatively, check `tx.origin` or use a trusted-forwarder pattern** — though `tx.origin` has its own risks; the explicit parameter approach is cleaner.

3. **At minimum**, document that pools using `SwapAllowlistExtension` must **not** allowlist the router, and that router-mediated swaps are incompatible with this extension — but this breaks the intended UX.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin allowlists address(router) so that allowlisted users can use the router
  - Pool admin does NOT allowlist attacker (address(0xDEAD))

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInput(...) targeting the restricted pool
  2. Router calls pool.swap(recipient=attacker, ...)
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓ (router was allowlisted)
  5. Swap executes — attacker successfully traded against restricted pool liquidity

Result:
  - allowedSwapper[pool][attacker] == false (never set)
  - But attacker bypassed the check entirely via the router
  - Any user can repeat this; the allowlist is nullified
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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
