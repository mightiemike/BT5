### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Per-User Allowlist Gate — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate `msg.sender` of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the actual end-user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for legitimate users), every user—including those not individually allowlisted—can bypass the per-user gate by routing through the router.

---

### Finding Description

`ExtensionCalling._beforeSwap` passes `msg.sender` of the pool's `swap` call as the `sender` argument to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist: [2](#0-1) 

Inside the hook, `msg.sender` is the pool and `sender` is the first argument—the immediate caller of `pool.swap`. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the router's address is what lands in `sender`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

This creates an irreconcilable dilemma for pool admins:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps blocked, even for individually allowlisted users |
| Router **allowlisted** | Every user bypasses the individual allowlist by routing through the router |

The allowlist mapping and the `isAllowedToSwap` view confirm the only two checks are `allowAllSwappers[pool]` and `allowedSwapper[pool][swapper]`—there is no mechanism to recover the original end-user's address: [3](#0-2) 

The pool's own audit target document explicitly flags this identity-binding gap as the critical validation focus for the swap allowlist gate: [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, trusted market makers, or to exclude adversarial actors from draining LP funds) cannot enforce that restriction when the router is involved. Any non-allowlisted user can execute swaps on the restricted pool by calling `MetricOmmSimpleRouter`, bypassing the intended access control entirely. LP funds are exposed to unauthorized trading on a pool whose entire security model depends on the allowlist being enforced.

**Allowed impact category:** Broken core pool functionality causing loss of funds or unusable swap flows; admin-boundary break where a factory/pool role check is bypassed by an unprivileged path.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is a public, permissionless contract reachable by any user. The bypass requires only that the pool admin has allowlisted the router—a natural and expected configuration step to allow legitimate users to trade via the router. No special privileges, malicious setup, or non-standard tokens are required.

---

### Recommendation

The extension must check the economically relevant actor, not the immediate pool caller. Concrete options:

1. **On-behalf-of field in `extensionData`:** Require the router to encode the original user's address in `extensionData`; the extension decodes and checks that address. The pool's `_beforeSwap` already forwards `extensionData` verbatim to every extension.
2. **Dual-address check:** Extend the allowlist to check both `sender` (the router) and a separately passed `originator` field, and require the router to supply it.
3. **Hard documentation constraint:** Document that the router must never be allowlisted and that allowlisted users must call the pool directly—accepting that router-mediated swaps are incompatible with per-user allowlisting.

Option 1 is the most practical without changing the core pool interface.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls: extension.setAllowedToSwap(pool, router, true)
   (allowlists the router so legitimate users can trade via it)
3. Pool admin does NOT call: extension.setAllowedToSwap(pool, attacker, true)
4. attacker calls: MetricOmmSimpleRouter.exactInput(pool, zeroForOne, amount, ...)
5. Router calls: pool.swap(attacker_recipient, zeroForOne, amount, ...)
   → msg.sender inside pool = router
6. Pool calls: extension.beforeSwap(router, attacker_recipient, ...)
   → sender = router
7. Extension evaluates: allowedSwapper[pool][router] == true → passes
8. Swap executes. attacker successfully trades on a pool they were
   individually excluded from, bypassing the allowlist gate entirely.
``` [2](#0-1) [1](#0-0)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-29)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
