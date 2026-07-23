### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the actual end-user, allowing any non-allowlisted user to bypass the swap gate by routing through `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

The `SwapAllowlistExtension` is a `beforeSwap` hook intended to restrict which addresses may trade against a pool. However, the `sender` argument it receives is `msg.sender` of the pool's `swap()` call — which is the **router contract** when users enter through `MetricOmmSimpleRouter`, not the actual end-user. If the router is added to the allowlist (a natural operational step so that normal users can trade), the allowlist check becomes a no-op: every user, regardless of their own allowlist status, can bypass the gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

The `IMetricOmmExtensions.beforeSwap` interface signature confirms `sender` is the first argument the extension receives: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter` (the public periphery entry point), the router calls `pool.swap()` on the user's behalf. At that point `msg.sender` inside the pool is the **router address**, not the originating user. The `SwapAllowlistExtension` therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

The research target for this path explicitly flags this identity mismatch: [4](#0-3) 

The analogous issue exists for `DepositAllowlistExtension` when deposits flow through `MetricOmmPoolLiquidityAdder`: [5](#0-4) 

---

### Impact Explanation

A pool operator deploys a pool with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). To allow normal trading flow, the operator adds `MetricOmmSimpleRouter` to the allowlist. Because the extension checks `sender == router` rather than the originating user, **every address on the network** can bypass the allowlist by calling the router. The intended access control is completely nullified. Non-allowlisted users can drain pool liquidity, execute arbitrage, or interact with pools that were designed to be permissioned — constituting a direct loss of the pool's access-control invariant and potential loss of LP principal if the pool was designed to trade only at favorable oracle prices with trusted counterparties.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- Adding the router to the allowlist is the expected operational step for any pool that wants to support normal user-facing swaps.
- No special privileges, flash loans, or unusual token behavior are required — any EOA can call the router.
- The bypass is reachable on every swap through the router on any allowlisted pool.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the true originator through `extensionData`**: The router should encode the original `msg.sender` into `extensionData` and the extension should decode and check that value. The pool's `_beforeSwap` already forwards `extensionData` unchanged to extensions.

2. **Alternatively, check `recipient`**: For direct swaps the recipient is often the user; however this is not reliable for all routing patterns.

3. **Preferred**: Require that allowlisted pools are only callable directly (not through the router), or introduce a dedicated `originSender` field in the extension hook interface so the pool can propagate the true initiator.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - allowedSwapper[pool][router] = true   (operator adds router for normal UX)
  - allowedSwapper[pool][alice] = false   (alice is NOT allowlisted)

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInput(pool, ...)
  2. Router calls pool.swap(recipient=alice, ..., extensionData)
     → msg.sender inside pool = router
  3. _beforeSwap passes sender=router to SwapAllowlistExtension.beforeSwap
  4. Extension checks allowedSwapper[pool][router] == true → passes
  5. alice's swap executes against the permissioned pool

Result:
  alice, a non-allowlisted address, successfully swaps against a pool
  that was configured to restrict trading to approved counterparties only.
  The SwapAllowlistExtension guard is completely bypassed.
``` [2](#0-1) [1](#0-0)

### Citations

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** generate_scanned_questions.py (L646-654)
```python
        Target(
            short="deposit allowlist gate",
            file_function="metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*",
            call_path="public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner",
            values="the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares",
            control_hint="The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate.",
            validation_focus="Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.",
        ),
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
