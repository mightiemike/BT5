### Title
`SwapAllowlistExtension` Checks `sender` (Router) Instead of the Actual User, Enabling Allowlist Bypass Through the Supported Router Path — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the **router** as `sender`, not the actual user. If the pool admin allowlists the router (the natural step to enable router-based swaps on a curated pool), every user — including those not individually allowlisted — can bypass the curation gate by going through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument the pool forwards — the `msg.sender` of the pool's own `swap()` call. The `IMetricOmmExtensions` interface confirms the pool passes `sender` and `recipient` as distinct parameters:

```solidity
function beforeSwap(
    address sender,
    address recipient,
    ...
) external returns (bytes4);
``` [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(recipient, ...)`, the pool's `msg.sender` is the **router**, so the extension receives `sender = router`. The allowlist therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates two symmetric failure modes:

| Scenario | Effect |
|---|---|
| Pool admin allowlists the router (to enable router-based swaps) | **Every user** can swap through the router, bypassing individual curation |
| Pool admin does NOT allowlist the router | **Every individually-allowlisted user** is blocked from using the supported router path |

The invariant stated in the audit vectors is violated in both cases:

> *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."* [3](#0-2) 

The `DepositAllowlistExtension` does not share this flaw — it ignores `sender` and checks `owner` (the LP position owner), which the pool receives as an explicit parameter from the caller and is expected to be the actual depositor:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
``` [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for KYC/curation purposes loses its enforcement guarantee the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()` targeting the curated pool and the extension will pass because it sees the allowlisted router as `sender`. The curation boundary is fully nullified without any admin action beyond the routine step of enabling router access.

---

### Likelihood Explanation

The router is the primary supported swap entrypoint for end users. A pool admin who deploys a curated pool and wants users to be able to use the standard router will naturally add the router to the allowlist. The misconfiguration is the expected operational path, not an edge case. No privileged attacker capability is required — any EOA with access to the router can exploit it.

---

### Recommendation

The extension must gate on the **economic actor**, not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `abi.encode(msg.sender)` into the per-pool `extensionData` slot. The extension decodes and checks that address. This requires a trusted encoding convention between the router and the extension.

2. **Check `recipient` for single-hop swaps, or require the pool to forward `tx.origin` / a signed user claim**: Less clean but avoids router-side changes. `tx.origin` is generally discouraged; a signed claim is more robust.

The cleanest fix is option 1: the router already accepts per-hop `extensionDatas` arrays, so the originating user address can be appended without breaking the existing interface.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as `beforeSwap` hook.
2. Call `setAllowedToSwap(pool, router, true)` — the natural step to allow router-based swaps.
3. As an address that is **not** individually allowlisted, call `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The extension receives `sender = router`, finds `allowedSwapper[pool][router] = true`, and returns the success selector.
5. The swap executes despite the caller never being individually approved.

Conversely, without step 2: call `setAllowedToSwap(pool, user, true)` for a specific user, then have that user attempt to swap through the router — the extension sees `sender = router`, finds `allowedSwapper[pool][router] = false`, and reverts with `NotAllowedToSwap`, blocking the individually-approved user from using the supported periphery path. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** generate_scanned_questions.py (L733-738)
```python
            title="allowlist bypass",
            question_focus="a curated pool's allowlist can be bypassed through a public router or liquidity-adder path",
            exploit="Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.",
            invariant="A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.",
            impact="High direct loss or curation failure if disallowed users can still trade or deposit.",
        ),
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
