### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals the pool's `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore gates the router's address rather than the actual economic actor. Any user — including those explicitly excluded from the allowlist — can bypass the restriction by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension hook) and `sender` is the address the pool forwards as the swap initiator — which is the pool's own `msg.sender`. The `IMetricOmmPoolActions` documentation confirms this: the error `NotAllowedToSwap` is described as "Swap allowlist rejected `msg.sender`", meaning the pool passes its own `msg.sender` directly as `sender` to the extension. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` on behalf of the user. At that point the pool's `msg.sender` is the router contract address, so the extension evaluates:

```
allowedSwapper[pool][router_address]
```

not

```
allowedSwapper[pool][end_user_address]
```

For a curated pool to be usable through the router at all, the pool admin must allowlist the router. Once the router is allowlisted, the check passes for **every** caller of the router, regardless of whether that caller is individually permitted. The allowlist is completely neutralised for router-mediated swaps.

The `generate_scanned_questions.py` research file explicitly identifies this as the primary attack surface for the swap allowlist gate:

> "Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting." [3](#0-2) 

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted market makers). Any non-allowlisted address can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The pool's liquidity — which LP depositors provided under the assumption that only approved counterparties could trade against it — is exposed to unrestricted public swaps. This constitutes a direct loss of the curation guarantee and can result in LP funds being traded against at unfavourable terms by actors the pool was explicitly designed to exclude.

**Severity: High** — direct bypass of a fund-protecting access control on a production pool path, reachable by any unprivileged address.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the periphery.
- Any pool using `SwapAllowlistExtension` that also needs to support router-mediated swaps must allowlist the router, which immediately opens the bypass.
- No special preconditions, privileged access, or flash loans are required. A single `exactInputSingle` call from any EOA suffices.
- The bypass is permanent as long as the router remains allowlisted.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the true end user, not the intermediary contract. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` (the end user) as the `sender` argument when calling `pool.swap`, rather than relying on the pool to use its own `msg.sender`. This requires the pool's `swap` interface to accept an explicit `sender` parameter, or the router must be trusted to forward the correct identity.

2. **In `SwapAllowlistExtension`**: if the pool's `swap` interface cannot be changed, the extension should require that `sender` is not a known router/intermediary, or the pool should pass the callback-verified payer identity rather than `msg.sender`.

The cleanest fix is for the pool to accept an explicit `sender` address in `swap` (similar to how `addLiquidity` separates `owner` from `msg.sender`/payer), so the router can forward the true end-user address and the extension can gate the correct actor.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router must be allowlisted for normal use
  - Pool admin does NOT call setAllowedToSwap(pool, eve, true)

Attack:
  1. Eve (not allowlisted) calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., tokenOut: ..., ...})
  2. Router calls pool.swap(...) — pool's msg.sender = router
  3. Pool calls extension.beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  → passes
  5. Eve's swap executes against the curated pool's liquidity

Result:
  Eve, an explicitly non-allowlisted address, successfully swaps against a
  pool whose allowlist was intended to exclude her. LP funds are traded
  against an unauthorized counterparty.
``` [1](#0-0) [2](#0-1) [4](#0-3)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
