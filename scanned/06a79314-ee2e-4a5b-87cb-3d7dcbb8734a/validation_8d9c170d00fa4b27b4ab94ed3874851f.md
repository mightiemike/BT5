### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via the public router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` at the pool level. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the router is allowlisted (a necessary step for any router-mediated swap to succeed), the allowlist gate is completely bypassed for every user who routes through the public router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient=msg.sender, ...)   // msg.sender here = router
              → _beforeSwap(sender=router, recipient=user, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`ExtensionCalling._beforeSwap` is invoked with `sender = msg.sender` (the pool's caller): [1](#0-0) 

The pool passes `msg.sender` as `sender` to every extension hook. When the call originates from the router, `msg.sender` at the pool is the router address, not the end user.

The router calls `pool.swap` with the actual user as `recipient`: [2](#0-1) 

The `SwapAllowlistExtension` is documented to key its check on `allowedSwapper[pool][sender]`: [3](#0-2) 

**The identity mismatch:** `sender` = router; actual economic actor = user (available as `recipient`). The extension never inspects `recipient`.

**Trigger condition:** The pool admin must allowlist the router to permit any router-mediated swap. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller regardless of who they are.

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC'd institutions, whitelisted market makers). The allowlist is the sole access-control layer for swaps. Any unpermissioned user bypasses it by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The pool's LP assets are exposed to unrestricted swap flow, breaking the invariant that only allowlisted addresses can trade against the pool's liquidity. This constitutes a broken core pool functionality with direct LP asset exposure.

---

### Likelihood Explanation

The bypass requires only that the router is allowlisted — a configuration any pool admin who wants to support router-mediated swaps for their allowlisted users must make. The router is a public, permissionless contract. No privileged access, no special token, and no malicious setup is required. Any ordinary EOA can execute the bypass in a single transaction.

---

### Recommendation

The extension should gate the **actual economic actor**, not the intermediary. Two sound options:

1. **Check `recipient` instead of `sender`** — when the router calls `pool.swap`, it passes the real user as `recipient`. The extension already receives `recipient` as a parameter.
2. **Require the actual user identity in `extensionData`** — the router already forwards `extensionData` unmodified; the extension can decode a user-supplied identity and verify it against `msg.sender` of the router call (passed via extensionData by the router).

Option 1 is simpler but requires confirming that `recipient` always equals the intended swapper across all call paths.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls allowedSwapper[pool][router] = true   // to enable router swaps for allowlisted users
3. Admin does NOT add attacker to allowedSwapper.
4. Attacker calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=attacker, ...).
6. Pool calls _beforeSwap(sender=router, recipient=attacker, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
8. Attacker's swap executes against the restricted pool's liquidity.
   allowedSwapper[pool][attacker] was never checked.
```

The attacker receives pool output tokens. LP assets are drained by an unpermissioned party. The allowlist invariant is broken with zero privileged access required. [1](#0-0) [2](#0-1) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
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
