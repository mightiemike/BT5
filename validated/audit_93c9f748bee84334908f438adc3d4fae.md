### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's own `msg.sender` at swap time. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. The allowlist therefore checks the router's address, not the actual swapper. If the pool admin allowlists the router (the only way to let legitimate users use the router), every unprivileged user can bypass the allowlist by routing through it.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User (EOA, not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → IMetricOmmPoolActions(params.pool).swap(recipient, ...)
          // pool's msg.sender = router address
          → MetricOmmPool._beforeSwap(msg.sender=router, ...)
              → ExtensionCalling._callExtensionsInOrder(...)
                  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                      // msg.sender = pool, sender = router
                      if (!allowAllSwappers[pool] && !allowedSwapper[pool][router]) revert
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim:

```solidity
// ExtensionCalling.sol:162-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
    sender,       // ← router address
    recipient,
    ...
))
```

`SwapAllowlistExtension.beforeSwap` then checks the router address against the allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The dilemma this creates for pool admins:**

| Admin action | Effect |
|---|---|
| Allowlist the router | Every user bypasses the allowlist via the router |
| Do NOT allowlist the router | Individually allowlisted users cannot use the router |

There is no configuration that simultaneously allows legitimate users to use the router AND blocks non-allowlisted users.

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to create a curated pool restricted to a set of approved counterparties (e.g., KYC'd addresses, institutional partners). Any non-allowlisted user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant). The pool's curation property is completely lost. Non-allowlisted users can trade against LP capital that was deposited under the assumption that only approved counterparties would interact with the pool. This constitutes a broken core pool functionality with direct LP fund impact (adverse selection, policy violation).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entrypoint documented in the protocol. Any user who discovers the mismatch — or simply uses the router as intended — bypasses the allowlist. No special privileges, flash loans, or multi-step setup are required. The trigger is a single standard router call.

---

### Recommendation

The `beforeSwap` hook must gate the **economically relevant actor** — the originating user — not the intermediate router. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension reads and verifies it. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the `recipient` (the address receiving output tokens) is often the economically relevant actor. However, `recipient` can also be set to a third party.

3. **Preferred — gate at the pool level with a dedicated field**: Add an `originator` field to the swap call that the pool populates from a trusted source (e.g., a transient-storage context set by the router before calling the pool), and pass that to extensions. This mirrors how `MetricOmmSimpleRouter` already stores the payer in transient storage for the callback.

Until fixed, pool admins should not rely on `SwapAllowlistExtension` for pools accessible via `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary for any user to use the router)
  - alice (EOA) is NOT individually allowlisted

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: alice,
       ...
     })
  2. Router calls pool.swap(alice, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes — alice bypassed the allowlist

Result:
  alice, a non-allowlisted user, successfully swaps on a curated pool.
  The allowlist invariant is broken for all router-mediated swaps.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
