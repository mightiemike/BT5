### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap` is called with `msg.sender = router`, so the extension receives `sender = router`. If the router address is on the allowlist (or if the pool is set to `allowAll`), any unpermissioned user can bypass the swap allowlist entirely by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.addLiquidity` and `swap` both call their respective extension hooks with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` (per the audit pivot description in `generate_scanned_questions.py`) performs an `allowAll / allowedSwapper` lookup **keyed by `(pool, sender)`**: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)` directly, making `msg.sender = router` from the pool's perspective. The extension therefore receives `sender = router address`, not the actual end-user address.

This creates two symmetric failure modes:

1. **Allowlist bypass (higher severity):** If the pool admin allowlists the router address (a natural operational choice so that router-mediated swaps work at all), every user — including those not individually allowlisted — can bypass the swap gate simply by routing through the public `MetricOmmSimpleRouter`.

2. **Broken core functionality (DoS):** If the router is *not* allowlisted, every individually-allowlisted user is silently blocked from using the router, making the primary swap interface unusable for the pool's intended participants.

The analog to the external bug is exact: the `onlyMasterPenPie` modifier blocked a second legitimate caller (`PendleVoteManager`); here the `allowedSwapper` check blocks the actual user because the check is applied to the intermediary (`MetricOmmSimpleRouter`) rather than the economic actor.

---

### Impact Explanation

**Bypass path:** A pool configured with a swap allowlist (e.g., a KYC-gated or institutional pool) is fully circumvented. Any address can execute swaps by calling `MetricOmmSimpleRouter` instead of the pool directly. The pool receives real token input and delivers real token output to the unauthorized swapper — direct loss of the allowlist invariant with fund-flow consequences.

**DoS path:** Allowlisted users who rely on the router (the primary periphery interface) cannot swap. Core pool swap functionality is broken for the intended user set.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface documented in the protocol.
- Pool admins who want router-mediated swaps to work will naturally allowlist the router, triggering the bypass.
- No privileged action or malicious setup is required; any public user can call the router.
- The misconfiguration is structural: the `sender` argument passed by the pool is always the immediate caller, never the end-user, so no configuration of the allowlist can simultaneously (a) allow router-mediated swaps and (b) gate individual users.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on `recipient` (the address that receives swap output, which the pool admin controls and which represents the economic beneficiary) rather than `sender` (the immediate caller). Alternatively, the router should forward the originating user address as part of `extensionData`, and the extension should decode and check that address. A third option is to add a separate `allowedRouter` mapping so that router-mediated swaps are checked against the `recipient` field.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured; set `allowAll = false`; allowlist only `userA`.
2. Also allowlist `MetricOmmSimpleRouter` so that router-mediated swaps are not immediately blocked.
3. Call `MetricOmmSimpleRouter.exactInputSingle(...)` as `userB` (not individually allowlisted), with `recipient = userB`.
4. The pool calls `_beforeSwap(msg.sender=router, recipient=userB, ...)`.
5. The extension checks `allowedSwapper[pool][router] == true` → passes.
6. `userB` receives token output despite never being allowlisted.

The allowlist invariant is broken: `userB` executed a swap that the pool admin intended to block. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
