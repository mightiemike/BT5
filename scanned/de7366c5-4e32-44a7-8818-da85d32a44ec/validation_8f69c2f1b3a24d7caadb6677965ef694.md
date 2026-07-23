### Title
`SwapAllowlistExtension` checks the router's address instead of the end user's address when swaps are routed through `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This wrong-actor binding either silently opens the allowlist to all router users (if the router is allowlisted) or silently blocks allowlisted users from using the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and uses `msg.sender` (the calling pool) as the pool key:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The `sender` argument is whatever the pool received as `msg.sender` when `pool.swap()` was called. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
``` [2](#0-1) 

The pool's `msg.sender` is the router contract, so the extension receives `sender = router address`. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The same pattern applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`. In the multi-hop case, intermediate hops use `address(this)` (the router itself) as the effective sender for all hops: [3](#0-2) 

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the LP position owner, explicitly passed through) rather than `sender` (the direct caller): [4](#0-3) 

No equivalent forwarding of the originating user exists on the swap path.

---

### Impact Explanation

**Scenario A — Allowlist bypass (Critical/High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a specific set of KYC'd or trusted addresses. To allow those users to use the router, the admin adds the router address to the allowlist (`setAllowedToSwap(pool, router, true)`). Because the router is a public, permissionless contract, any address can call `exactInputSingle` through it. The extension sees `sender = router` and passes the check for every caller, completely defeating the per-user curation. All user principal flowing through the pool is now accessible to unapproved swappers.

**Scenario B — Broken core functionality (High):** A pool admin allowlists individual user addresses for direct `pool.swap()` calls. Those users cannot swap through the router because the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`. The router — the primary production entry point — is permanently broken for all allowlisted users on that pool.

Both scenarios represent either direct loss of curation policy (Scenario A) or broken core swap functionality (Scenario B) above Sherlock thresholds.

---

### Likelihood Explanation

Any pool that:
1. Deploys `SwapAllowlistExtension` (a production extension in the periphery), **and**
2. Expects users to interact via `MetricOmmSimpleRouter` (the primary production router)

is affected. This is the expected production configuration. The trigger requires no privileged access — any public user calling the router reaches the vulnerable path. The `generate_scanned_questions.py` audit target explicitly flags this exact call path:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [5](#0-4) 

---

### Recommendation

Pass the originating user through the swap call so the extension can check the economically relevant actor. Two approaches:

1. **Preferred — forward `msg.sender` from the router as a dedicated `sender` argument to `pool.swap`:** The pool already receives a `recipient`; add a separate `sender` field to the swap interface that the router populates with `msg.sender` before calling the pool. The pool forwards this to the extension's `beforeSwap`.

2. **Alternative — mirror the deposit extension pattern:** Have the extension check a field that is explicitly set to the originating user (analogous to how `DepositAllowlistExtension` checks `owner`, not `sender`). This requires the router to encode the originating user in `extensionData` and the extension to decode and verify it — which introduces its own trust assumptions.

Option 1 is cleaner and consistent with how `DepositAllowlistExtension` already handles the owner/payer separation correctly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the intended curated user)
  - allowedSwapper[pool][router] = false (router is not explicitly allowlisted)

Attack path (Scenario A — bypass):
  1. Admin sets allowedSwapper[pool][router] = true to let alice use the router
  2. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  3. Router calls pool.swap(recipient=bob, ...)  →  msg.sender = router
  4. Pool calls extension.beforeSwap(sender=router, ...)
  5. allowedSwapper[pool][router] == true  →  check passes
  6. Bob's swap executes despite not being on the allowlist

Attack path (Scenario B — broken functionality):
  1. Admin sets allowedSwapper[pool][alice] = true
  2. Alice calls router.exactInputSingle({pool: pool, ...})
  3. Router calls pool.swap(recipient=alice, ...)  →  msg.sender = router
  4. Pool calls extension.beforeSwap(sender=router, ...)
  5. allowedSwapper[pool][router] == false  →  revert NotAllowedToSwap
  6. Alice cannot use the router despite being allowlisted
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
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

**File:** generate_scanned_questions.py (L659-663)
```python
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
