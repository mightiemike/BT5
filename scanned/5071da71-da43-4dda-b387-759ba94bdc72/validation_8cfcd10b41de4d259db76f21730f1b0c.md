### Title
`SwapAllowlistExtension` Bypass via `MetricOmmSimpleRouter` — Router Allowlisting Grants Unrestricted Swap Access to All Users - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

The `SwapAllowlistExtension` gates swaps by checking `sender`, which is the direct `msg.sender` of `pool.swap()`. When the pool admin allowlists the `MetricOmmSimpleRouter` to enable router-mediated swaps for approved users, any unprivileged user can bypass the allowlist entirely by routing through the public, permissionless router — because the extension sees `sender = router` (allowlisted) rather than the actual end-user.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the address the pool forwards — which is `msg.sender` of the pool's own `swap()` call. The pool's `swap()` interface accepts no explicit `sender` parameter:

```solidity
function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
) external returns (int128 amount0Delta, int128 amount1Delta);
``` [2](#0-1) 

The pool therefore uses `msg.sender` as `sender` when it calls `_beforeSwap`:

```solidity
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
         packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
    )
);
``` [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` inside the pool is the **router address**, so the extension receives `sender = router`. If the pool admin has set `allowedSwapper[pool][router] = true` — a natural step to enable router-mediated swaps for approved users — the allowlist check passes unconditionally for every caller of the public router, regardless of whether the actual end-user is on the allowlist.

The research pivot confirms this identity-binding gap:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

The `SwapAllowlistExtension` is keyed by `sender`, not by the originating user:

```
call_path: "public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender"
``` [5](#0-4) 

---

### Impact Explanation

Any user — including those explicitly excluded from the allowlist — can swap in a pool that was configured to be restricted to specific counterparties. In institutional or permissioned pool deployments, this allows unauthorized parties to drain favorable liquidity, manipulate pool state, or extract value from LP positions, constituting a direct loss of LP principal and a broken core access-control invariant.

---

### Likelihood Explanation

Medium. The trigger is a pool admin allowlisting the `MetricOmmSimpleRouter` — a routine, non-malicious action taken to let approved users benefit from router UX (multi-hop, slippage helpers, etc.). The admin has no reason to suspect this opens the allowlist to all users. The `MetricOmmSimpleRouter` is a public, permissionless periphery contract; once the router address is allowlisted, the bypass is available to any EOA or contract with no further preconditions.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData`; `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. The pool already forwards `extensionData` to every extension hook.
2. **No router allowlisting**: Document that the router must never be added to the allowlist; individual user addresses must be allowlisted directly. Enforce this with a registry check that rejects known periphery contract addresses.

---

### Proof of Concept

**Setup:**
- Pool deployed with `SwapAllowlistExtension` configured.
- Pool admin calls `setAllowedToSwap(pool, router, true)` to let approved users access the pool via the router.
- Alice (`0xAlice`) is **not** on the allowlist.

**Attack:**
1. Alice calls `MetricOmmSimpleRouter.exactInput(pool, ...)`.
2. Router calls `pool.swap(recipient=Alice, ...)` — `msg.sender` inside the pool is `router`.
3. Pool calls `_beforeSwap(sender=router, ...)`.
4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
5. Swap executes. Alice receives output tokens despite never being allowlisted.

**Invariant broken:** `allowedSwapper[pool][Alice] == false`, yet Alice successfully swaps — the allowlist is silently bypassed through the router intermediary, exactly as a compromised chain in Axelar bypasses balance limits by routing through an untracked token manager on a third chain. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
```

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
