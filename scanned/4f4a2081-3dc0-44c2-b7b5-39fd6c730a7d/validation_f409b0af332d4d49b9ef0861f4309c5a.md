### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass per-user swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` in the pool is the router, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users simultaneously opens the gate for every unpermitted user who routes through the same router.

---

### Finding Description

**Root cause — wrong actor binding in `beforeSwap`**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension caller). `sender` is whatever the pool passes as the first argument to the hook — which is `msg.sender` of `pool.swap()`, i.e., the direct caller of the pool. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool sees `msg.sender = router`, so the extension receives `sender = router` and checks `allowedSwapper[pool][router]`.

**Contrast with `DepositAllowlistExtension`**

`DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the first argument (the direct caller / payer) and checks `owner` — the economically attributed actor:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The deposit extension is correct; the swap extension is not.

**Attack path**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise vetted addresses.
2. Pool admin allowlists `MetricOmmSimpleRouter` so that permitted users can swap via the router (without this, no router-mediated swap can succeed).
3. An unpermitted user calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
4. The router calls `pool.swap(recipient = attacker, ...)`. Inside the pool, `msg.sender = router`.
5. The pool dispatches `extension.beforeSwap(sender = router, ...)`.
6. The extension evaluates `allowedSwapper[pool][router]` → `true` (router is allowlisted).
7. The swap executes. The unpermitted user receives output tokens from the curated pool.

The allowlist is completely bypassed. Every unpermitted user who routes through the same router gains the same access as a vetted user.

**Dilemma for pool admins**

There is no safe configuration:
- Allowlist the router → all users bypass the per-user gate.
- Do not allowlist the router → no user can use the router with this pool (broken core functionality for the supported periphery path).

---

### Impact Explanation

**Severity: High**

A curated pool's primary access-control mechanism — the swap allowlist — is rendered ineffective for any user who routes through `MetricOmmSimpleRouter`. Consequences include:

- Unauthorized users trade on pools restricted to vetted counterparties (KYC, whitelist, institutional-only).
- If the pool offers favorable pricing or subsidized liquidity for allowlisted users, unpermitted users extract that value directly.
- The pool admin's curation policy is silently voided without any on-chain signal.

This is a direct loss of the pool's intended access-control invariant and falls under "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" and "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path."

---

### Likelihood Explanation

**Likelihood: High**

- No privileged access is required. Any EOA can call `MetricOmmSimpleRouter`.
- The router is a supported, production periphery contract explicitly documented as the standard swap entry point.
- The bypass is deterministic and requires no timing, oracle manipulation, or special state.
- Pool admins who want router-mediated swaps to work *must* allowlist the router, making the bypass inevitable.

---

### Recommendation

The `beforeSwap` hook must check the economically attributed actor — the end user — not the direct caller of `pool.swap()`. Two options:

**Option A (preferred):** Pass the original user through the router as a separate argument or via `extensionData`, and have the extension decode and check that address. The pool or router must commit to a canonical "originator" field that extensions can trust.

**Option B:** Mirror the deposit extension pattern — have the pool pass a dedicated "originator" address (analogous to `owner` in `addLiquidity`) that the router sets to `msg.sender` before calling the pool, and have the extension check that field instead of `sender`.

In either case, the invariant must be: **the identity checked by the allowlist is the address that economically initiates and benefits from the swap, not the intermediary contract that relays the call.**

---

### Proof of Concept

```
Setup:
  pool = curated pool with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin must set this for router swaps to work
  allowedSwapper[pool][alice]  = true   // alice is a vetted user
  allowedSwapper[pool][bob]    = false  // bob is NOT permitted

Attack:
  bob calls MetricOmmSimpleRouter.exactInput({
      path: encode(pool, ...),
      recipient: bob,
      amountIn: X,
      ...
  })

  Router calls pool.swap(recipient=bob, ...)
    → pool: msg.sender = router
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] → true
    → swap executes, bob receives output tokens

Result:
  bob successfully swaps on a pool he is explicitly not permitted to use.
  The allowlist check never evaluated bob's address.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```
