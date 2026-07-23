### Title
SwapAllowlistExtension gates the router contract address instead of the actual user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (a natural step to enable router-based swaps for legitimate users), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

The call chain is:

1. `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(params.recipient, ...)` with `msg.sender = router`. [1](#0-0) 

2. `MetricOmmPool.swap` forwards `msg.sender` (the router) as the `sender` argument to `_beforeSwap`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` encodes and dispatches that same `sender` value to every configured extension. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` receives `sender` = router address and checks `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`. [4](#0-3) 

The extension has no visibility into who called the router. The allowlist is keyed on `(pool, sender)` where `sender` collapses to the router address for every user who routes through it. [5](#0-4) 

---

### Impact Explanation

Two broken outcomes, both fund-impacting:

**Path A — Router is allowlisted (allowlist fully bypassed):**  
A pool admin who wants legitimate users to swap via the router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, *any* address can call `router.exactInputSingle(pool, ...)` and the extension passes unconditionally. The per-user curation is completely voided. Unauthorized users can drain oracle-priced liquidity from a pool that was intended to be restricted (e.g., KYC-gated, counterparty-restricted, or regulatory-scoped).

**Path B — Router is not allowlisted (allowlisted users broken):**  
Allowlisted users who attempt to swap through the router receive `NotAllowedToSwap` because `allowedSwapper[pool][router]` is false. The supported periphery path is unusable for the pool's intended participants, breaking core swap functionality.

Both paths represent a broken invariant: the allowlist cannot simultaneously permit router-based swaps and enforce per-user identity.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary supported swap entrypoint for EOAs.
- Pool admins who configure `SwapAllowlistExtension` will naturally need to decide whether to allowlist the router; either choice produces a broken outcome.
- No special privilege, malicious setup, or non-standard token is required. Any unprivileged user can trigger Path A by calling the public router after the admin has allowlisted it.
- The `FullMetricExtensionTest` only tests direct pool calls (via `TestCaller`), not router-mediated swaps, so the bypass is untested and undetected. [6](#0-5) 

---

### Recommendation

The `beforeSwap` hook must gate the **actual user**, not the intermediary. Two options:

1. **Pass real user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention and is fragile.

2. **Check `recipient` instead of `sender` for swap allowlisting**: The recipient is the economic beneficiary of the swap. If the pool's curation intent is to restrict who *benefits*, gating on `recipient` is more robust and is already passed through the hook signature.

3. **Preferred — dedicated router-aware allowlist**: The extension should accept a trusted-forwarder pattern where the router attests the real caller, or the pool admin allowlists individual users (not the router) and requires direct pool calls only.

Regardless of approach, add integration tests that exercise `SwapAllowlistExtension` through `MetricOmmSimpleRouter` with both allowlisted and non-allowlisted callers.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin allowlists Alice: setAllowedToSwap(pool, alice, true)
  - Pool admin also allowlists the router so Alice can use it:
      setAllowedToSwap(pool, router, true)

Attack (Bob, not allowlisted):
  1. Bob calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes for Bob despite Bob not being on the allowlist

Result:
  Bob swaps in a curated pool, bypassing the per-user allowlist entirely.
  If the pool holds oracle-priced liquidity, Bob can extract value that
  the pool admin intended to restrict to allowlisted counterparties only.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` line 37: `allowedSwapper[msg.sender][sender]` where `sender` is the router address, not the actual user, whenever the supported periphery path is used. [7](#0-6)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

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
