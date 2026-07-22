### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via the Public Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` equals the **router's address**, not the actual user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for legitimate users), every unprivileged user can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user goes through `MetricOmmSimpleRouter`, `sender` = **router address**. The check therefore becomes `allowedSwapper[pool][router]`.

The pool admin who wants legitimate users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the guard is satisfied for **every caller** of the router, regardless of whether that caller is individually allowlisted. A non-allowlisted user simply calls `MetricOmmSimpleRouter.exact*` targeting the restricted pool and the extension passes.

The `generate_scanned_questions.py` audit target explicitly flags this path:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access to a pool. Bypassing it allows non-allowlisted addresses to execute swaps in pools that were intentionally restricted (e.g., KYC-gated, institutional-only, or pre-launch pools). This is a direct admin-boundary break: a pool admin-configured guard is defeated by an unprivileged path through a public periphery contract. Depending on pool purpose, consequences include unauthorized token flows, regulatory violations, and loss of LP principal if the pool was restricted precisely to prevent adverse selection.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. The only precondition is that the pool admin has added the router to the allowlist — a routine operational step for any pool that intends to support router-mediated swaps for its legitimate users. Any user who is aware of the router address can exploit this immediately at zero cost beyond gas.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass user identity through `extensionData`**: The router encodes the actual user's address into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, so the router itself must be verified.
2. **Check `owner`/`recipient` instead of `sender`**: For swaps, the `recipient` is the address receiving output tokens and is a better proxy for the economic actor than the routing contract.

The `DepositAllowlistExtension` should be audited for the same pattern, since `beforeAddLiquidity` also receives a `sender` that will be the `MetricOmmPoolLiquidityAdder` address when deposits are routed through the adder.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension; allowAll = false.
2. Admin calls setAllowedToSwap(pool, alice, true)       // Alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)      // Enables router for Alice
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
   targeting the restricted pool.
5. Router calls pool.swap(recipient=bob, ...) → msg.sender = router.
6. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
7. Check: allowedSwapper[pool][router] == true  → passes.
8. Bob's swap executes in the restricted pool.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at the identity check on line 37, which resolves to the router address rather than the actual user whenever the public router is the intermediary. [5](#0-4)

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
