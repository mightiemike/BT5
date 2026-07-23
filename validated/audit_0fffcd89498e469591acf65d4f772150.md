### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The allowlist therefore gates the router's address rather than the actual trader. If the router is allowlisted (a natural admin action for a trusted periphery), every user—including those not individually allowlisted—can bypass the curation policy by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool): [2](#0-1) 

When a user calls the pool **directly**, `sender = user` and the check is `allowedSwapper[pool][user]` — correct.

When a user calls through `MetricOmmSimpleRouter`, the router calls `pool.swap(recipient, ...)` as `msg.sender`, so `sender = router` and the check becomes `allowedSwapper[pool][router]`.

The pool's own documentation acknowledges the operator pattern for liquidity but the same actor-separation applies to swaps: [3](#0-2) 

The `generate_scanned_questions.py` audit target explicitly flags this as the "wrong-actor binding" vector: [4](#0-3) 

---

### Impact Explanation

**Scenario A — Router is allowlisted (bypass):**
A pool admin who wants to allow all router-mediated swaps sets `allowedSwapper[pool][router] = true`. Every user, including those not individually allowlisted, can now trade on the curated pool by routing through `MetricOmmSimpleRouter`. The individual-user allowlist is completely nullified. This is a direct curation failure: disallowed users trade on a pool that was designed to restrict access.

**Scenario B — Router is not allowlisted (broken functionality):**
A pool admin allowlists specific KYC'd users by address. Those users cannot use the router because the extension sees `allowedSwapper[pool][router]` (false) and reverts `NotAllowedToSwap`. Allowlisted users are forced to call the pool directly, breaking the standard swap UX and any multi-hop routing.

Both scenarios represent a broken invariant: the allowlist guard does not enforce the same policy regardless of which supported public entrypoint reaches the pool. [5](#0-4) 

---

### Likelihood Explanation

- Allowlisting the router is a natural and expected admin action for any pool that wants to support the standard periphery while still restricting direct pool access.
- The router is a deployed, public, permissionless contract — any user can call it.
- No special privileges, no malicious setup, and no non-standard tokens are required. Any user with a valid swap amount can trigger the bypass in a single transaction.
- The `FullMetricExtensionTest` integration test exercises the allowlist only with direct pool calls (`_swap` calls the pool directly via `TestCaller`), so the router-mediated path is untested and the bypass is not caught by existing tests. [6](#0-5) 

---

### Recommendation

The `beforeSwap` hook must gate the **end user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original initiator through the router.** The router encodes the original `msg.sender` into `extensionData` and the extension decodes it. The pool's `_beforeSwap` already forwards `extensionData` unchanged, so no core changes are needed.

2. **Check `recipient` instead of `sender` when `sender` is a known router.** Less clean but avoids encoding changes.

The `DepositAllowlistExtension` already uses `owner` (not `sender`) for the deposit path, which is the correct pattern for the liquidity side: [7](#0-6) 

The swap allowlist should adopt an equivalent pattern — checking the economically relevant actor (the end user who benefits from the swap output) rather than the intermediate contract.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin allowlists the router as trusted periphery
  allowedSwapper[pool][alice]  = false  // alice is NOT individually allowlisted
  allowedSwapper[pool][bob]    = true   // bob IS individually allowlisted

Attack (alice bypasses the allowlist):
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
    → router calls pool.swap(recipient=alice, ...)   // msg.sender to pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] == true    ✓ PASSES
    → alice's swap executes on the curated pool

Expected: revert NotAllowedToSwap (alice is not allowlisted)
Actual:   swap succeeds — allowlist bypassed
```

The bypass requires zero privileged access: alice is an ordinary unprivileged user calling a public router function. The only precondition is that the pool admin has allowlisted the router, which is the natural configuration for any pool that intends to support the standard periphery. [2](#0-1) [1](#0-0)

### Citations

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-150)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
```

**File:** generate_scanned_questions.py (L718-724)
```python
        Vector(
            title="wrong-actor binding",
            question_focus="the hook checks the wrong actor among sender, owner, payer, or recipient",
            exploit="Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.",
            invariant="Every guard must key authorization to the same actor that the economic action is actually attributed to.",
            impact="High direct loss or policy bypass on curated pools.",
        ),
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
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
