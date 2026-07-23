### Title
`SwapAllowlistExtension` gates on router address instead of actual user when swaps are routed through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is `msg.sender` of the pool's `swap` call — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users simultaneously opens the gate to every unpermitted user who routes through the same public contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is:

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

`msg.sender` here is the pool (enforced by `onlyPool` in `BaseMetricExtension`). `sender` is the first argument forwarded by the pool's `_beforeSwap` dispatcher:

```solidity
function _beforeSwap(
    address sender,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

The pool passes its own `msg.sender` (the direct caller of `pool.swap()`) as `sender`. When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap()`, making `sender = router address`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool's own error documentation confirms this identity:

> `NotAllowedToSwap` — Swap allowlist rejected `msg.sender`. [3](#0-2) 

The protocol's own audit-target document explicitly flags this path:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*
> *"Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract."* [4](#0-3) 

The admin faces an irresolvable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| Allowlist the router | Every non-allowlisted user can bypass the gate via the router |

There is no configuration that simultaneously permits router-mediated swaps for approved users and blocks unapproved users.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool intends to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers). Once the router is allowlisted — a necessary operational step for any user who needs router multi-hop routing — every unpermitted address can bypass the gate by calling `MetricOmmSimpleRouter` instead of the pool directly. Unauthorized traders gain access to pool liquidity, exposing LPs to adverse selection, front-running, or value extraction that the allowlist was designed to prevent. This is a direct LP-fund-impacting consequence of the broken guard.

---

### Likelihood Explanation

The bypass is reachable by any unprivileged user with no special role or setup beyond calling the public router. The only precondition is that the pool admin has allowlisted the router — a routine operational step for any pool that expects users to interact via the standard periphery. The router is a public, permissionless contract, so the bypass is trivially reproducible once that allowlist entry exists.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original user's identity**, not the intermediary's. Two viable approaches:

1. **Pass the original caller in `extensionData`**: Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it (with a signature or trusted-forwarder pattern).
2. **Check `tx.origin` as a fallback** (only if the pool is not used in meta-transaction contexts): replace `sender` with `tx.origin` inside the extension when `sender` is a known router.
3. **Preferred — mirror the deposit allowlist pattern**: The `DepositAllowlistExtension` correctly gates on `owner` (the position owner, not the caller), because `addLiquidity` separates payer from owner. The swap path should similarly expose the intended economic actor — the user — separately from the routing intermediary. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension in beforeSwap order
  admin calls setAllowedToSwap(pool, userA, true)       // only userA is permitted
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted for userA's convenience

Attack:
  userB (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  → router calls pool.swap(recipient, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
  → swap succeeds — userB bypasses the allowlist entirely

Expected: revert NotAllowedToSwap
Actual:   swap executes, userB trades on a restricted pool
``` [1](#0-0) [2](#0-1)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
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
