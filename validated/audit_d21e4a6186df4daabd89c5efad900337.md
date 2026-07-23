### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Full Allowlist Bypass via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **end user** is allowlisted. If the pool admin allowlists the router to support router-mediated swaps for permitted users, every unprivileged user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Pool → Extension sender binding**

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**SwapAllowlistExtension check**

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]`: [3](#0-2) 

**Router call site**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making `msg.sender` of `pool.swap()` the router contract, not the end user: [4](#0-3) 

**The mismatch**

| Caller path | `sender` seen by extension | Allowlist entry needed |
|---|---|---|
| Alice calls `pool.swap()` directly | `alice` | `allowedSwapper[pool][alice]` |
| Alice calls `router.exactInputSingle()` | `router` | `allowedSwapper[pool][router]` |
| Bob calls `router.exactInputSingle()` | `router` | `allowedSwapper[pool][router]` |

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users (Alice) cannot use the router at all.
- **Allowlist the router** → every user (including Bob) can bypass the allowlist through the router.

There is no configuration that simultaneously allows Alice to use the router and blocks Bob from using it.

---

### Impact Explanation

Any user can bypass a `SwapAllowlistExtension`-gated pool by routing through `MetricOmmSimpleRouter` once the pool admin has allowlisted the router. Unauthorized swaps in a restricted pool can extract value from LPs at oracle-quoted prices, drain one side of the pool, or circumvent access controls the pool admin believed were enforced. This is a direct loss-of-principal risk for LPs in pools that rely on the allowlist as their primary access gate.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router — a natural and expected configuration step for any pool that wants to support standard periphery tooling. The research target description explicitly flags this path: [5](#0-4) 

Once the router is allowlisted (a one-time admin action), the bypass is trivially reachable by any unprivileged user with no special knowledge or capital beyond the swap amount.

---

### Recommendation

The extension must gate the **economically relevant actor** (the end user), not the intermediate dispatcher (the router). Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. The extension must also verify that the claim comes from a trusted router (e.g., via a factory-registered router registry), otherwise any caller can forge the identity.

2. **Separate `originator` field in the pool interface**: Add an `originator` parameter to `pool.swap()` that the router sets to `msg.sender` and the pool forwards to extensions alongside `sender`. Extensions can then choose which identity to gate.

Until one of these is implemented, the `SwapAllowlistExtension` cannot safely coexist with the public router on a pool that intends to restrict individual swappers.

---

### Proof of Concept

```
Setup:
  pool = restricted pool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)
  admin calls setAllowedToSwap(pool, router, true)   ← required for Alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender of pool.swap() = router

  pool calls:
    extension.beforeSwap(sender=router, ...)

  extension checks:
    allowedSwapper[pool][router] == true  →  passes

  Result: Bob swaps successfully in a pool he was never authorized to access.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
