### Title
`SwapAllowlistExtension` Gates Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool against `allowedSwapper[pool][sender]`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address rather than the actual end-user. Any user can bypass a curated pool's swap allowlist by routing through the public router instead of calling the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist keyed by `msg.sender` (the calling pool):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The `sender` value the pool passes into this hook is the `msg.sender` of the `pool.swap()` call — confirmed by the `Swap` event NatDoc: *"`sender` — `msg.sender` of `swap`"*. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput(...)` (or any router entry point), the router calls `pool.swap(...)` directly. At that point:

- `pool.swap()` sees `msg.sender = router`
- The pool passes `sender = router` to `extension.beforeSwap(...)`
- The extension evaluates `allowedSwapper[pool][router]`

**Scenario A — Router is allowlisted:** The pool admin must allowlist the router to permit any router-mediated swap. Once the router is allowlisted, every user — including those the admin explicitly excluded — can bypass the allowlist by routing through the router.

**Scenario B — Router is not allowlisted:** No user can swap through the router on that pool, breaking the intended periphery integration entirely.

There is no middle ground: the check cannot simultaneously gate individual end-users and permit router-mediated swaps, because the router collapses all user identities into a single address.

The `DepositAllowlistExtension` has an analogous structure — it checks the `owner` argument passed by the pool, which is the `owner` parameter of `addLiquidity(owner, salt, ...)`. Because `msg.sender` (the payer) need not equal `owner`, a non-allowlisted payer can deposit into an allowlisted owner's position, or the allowlist can be circumvented through the liquidity adder's operator pattern. [3](#0-2) 

---

### Impact Explanation

**Severity: High**

The swap allowlist is the primary access-control mechanism for curated pools. Its complete bypass by any unprivileged user routing through the public `MetricOmmSimpleRouter` means:

- Disallowed users can execute swaps on restricted pools, draining LP value at oracle-quoted prices.
- Pool operators who deploy allowlisted pools for compliance, KYC, or partner-only access have no effective protection once the router is allowlisted.
- LP principal is at direct risk: unauthorized swappers can extract token0/token1 from bins at the oracle mid-price, causing real fund loss to LPs who deposited under the assumption that only approved counterparties could trade.

This matches the allowed impact gate: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path"* and *"Broken core pool functionality causing loss of funds."*

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the standard, publicly documented entry point for swaps. Any user aware of the allowlist restriction can trivially route through the router. No special privileges, flash loans, or multi-step setup are required — a single `exactInput` call suffices. [4](#0-3) 

---

### Recommendation

The extension must resolve the true end-user identity rather than trusting the `sender` argument blindly. Two sound approaches:

1. **Pass-through identity via trusted router:** Have the router encode the original `msg.sender` inside `extensionData` and have the extension verify that the pool's `msg.sender` is a factory-registered router before reading the embedded identity. This requires the extension to know which routers are trusted.

2. **Check `tx.origin` as a fallback for router paths:** If `sender` is a known trusted router, fall back to `tx.origin` for the allowlist lookup. This is simpler but has known limitations with smart-contract wallets.

3. **Require direct pool calls for allowlisted pools:** Document and enforce that allowlisted pools must not register the router as an allowed swapper, and provide a separate allowlist-aware router that forwards the original caller identity.

The cleanest fix mirrors the external report's recommendation: at the start of `beforeSwap`, if `sender` is a registered periphery router, extract and check the real caller from `extensionData` rather than using `sender` directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowlisted for normal use
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInput({
         path: [token0, pool, token1],
         recipient: attacker,
         amountIn: X,
         amountOutMinimum: 0,
         extensionData: ""
     })
  2. Router calls pool.swap(attacker, zeroForOne, X, 0, "")
  3. Pool calls extension.beforeSwap(router, attacker, ...)
     → allowedSwapper[pool][router] == true  → passes
  4. Swap executes; attacker receives token1 output.

Result:
  - attacker, who was never allowlisted, successfully swapped on a curated pool.
  - LP funds were consumed at oracle price by an unauthorized counterparty.
``` [5](#0-4)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L53-54)
```text
  /// @param sender `msg.sender` of `swap` (the account that may receive swap callback).
  /// @param recipient Address that received the output token leg.
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-162)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
  /// @param callbackData Opaque bytes forwarded unmodified to the modify-liquidity callback.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Total token0 actually pulled from the callback into the pool (native).
  /// @return amount1Added Total token1 actually pulled from the callback into the pool (native).
  /// @dev Reverts `LiquidityDeltaLengthMismatch` when `binIdxs` and `shares` lengths differ.
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (uint256 amount0Added, uint256 amount1Added);
```
