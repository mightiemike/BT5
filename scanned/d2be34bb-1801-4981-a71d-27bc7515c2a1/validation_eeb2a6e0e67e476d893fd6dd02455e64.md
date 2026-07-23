Based on my investigation of the codebase, I can identify a valid analog vulnerability in the swap allowlist guard path.

---

### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` = router address. If the router is allowlisted (required for normal router-mediated swaps), every user — including those not individually allowlisted — can bypass the restriction by calling the public router. The guard is applied to the wrong identity.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

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

Here `msg.sender` is the pool (correct), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
    )
);
```

The pool sets `sender = msg.sender` of its own `swap` entry point. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)`, making the pool's `msg.sender` — and therefore `sender` — the **router address**, not the end user.

The allowlist check therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Scenario A — Router is allowlisted:** The pool admin adds the router to the allowlist so that normal router-mediated swaps work. Every user, including those the admin explicitly did not allowlist, can now bypass the restriction by calling the router. The allowlist is effectively nullified for all router paths.

**Scenario B — Router is not allowlisted:** No user can swap through the router even if individually allowlisted, breaking the intended operator pattern and making the pool unusable via the standard periphery.

There is no path through the public periphery that preserves the intended per-user identity check.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., a private OTC pool, a KYC-gated pool, or a pool that only allows trusted market makers) can be freely accessed by any address via `MetricOmmSimpleRouter`. Unauthorized swaps against a pool with concentrated liquidity can extract value from LPs at oracle-anchored prices, constituting a direct loss of LP principal. This is a broken core pool access-control invariant with fund-impacting consequences.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed swap entry point documented in the periphery README. Any user aware of the allowlist restriction can trivially route through the router to bypass it. No privileged access, special tokens, or malicious setup is required — only a standard `exactInputSingle` call.

### Recommendation

The pool must forward the **original caller's identity** through the router so the extension can gate the correct actor. Two standard approaches:

1. **Pass-through sender field:** `MetricOmmSimpleRouter` should encode the original `msg.sender` into `extensionData` and the extension should decode and check it — but this requires the extension to trust the router, which reintroduces the same problem unless the router is verified on-chain.

2. **Preferred — Pool-level sender forwarding:** The pool's `swap` function should accept an explicit `sender` parameter (the original user) rather than using `msg.sender`, and the router should pass `msg.sender` as that argument. The pool then forwards the declared sender to extensions. This is the pattern used by Uniswap v4's `PoolManager` where the router attests the originating caller.

Until fixed, pools relying on `SwapAllowlistExtension` for access control should not use `MetricOmmSimpleRouter` as an allowed entry point.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, routerAddress, true)` to enable router-mediated swaps for allowlisted users.
3. Admin does **not** call `setAllowedToSwap(pool, attackerAddress, true)`.
4. Attacker calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, recipient, ...)` → extension checks `allowedSwapper[pool][router]` → **passes**.
7. Attacker's swap executes against the pool's LP liquidity at oracle price, bypassing the per-user allowlist entirely.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
