### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the pool to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router calls `pool.swap(...)` with `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The admin API stores allowances keyed by the intended swapper:

```solidity
function setAllowedToSwap(address pool_, address swapper, bool allowed)
    external onlyPoolAdmin(pool_)
{
    allowedSwapper[pool_][swapper] = allowed;
}
```

If the pool admin allowlists individual users (e.g., `allowedSwapper[pool][alice] = true`) but not the router, those users cannot swap via the router at all — the check fails because `allowedSwapper[pool][router]` is `false`. To restore router access for legitimate users the admin must allowlist the router itself (`allowedSwapper[pool][router] = true`), which immediately grants every user on the network the ability to bypass the allowlist by routing through the public `MetricOmmSimpleRouter`.

The `generate_scanned_questions.py` research file explicitly flags this path:

> "Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers). Once the router is allowlisted — a necessary step if any legitimate user needs router access — the restriction is completely nullified. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and swap against the pool. This constitutes a broken core pool functionality (the allowlist guard) with direct fund-impacting consequences: unauthorized parties can drain liquidity or execute swaps the pool admin explicitly intended to block.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract; any EOA or contract can call it.
- The pool admin has no way to simultaneously (a) allow legitimate users to use the router and (b) block illegitimate users from using the router, because the extension sees only the router's address.
- The admin is therefore forced into a binary choice: block all router-mediated swaps (breaking UX for legitimate users) or allowlist the router (opening the pool to everyone). Either outcome is harmful.
- No special privilege or setup is required for the attacker beyond knowing the pool address.

---

### Recommendation

The extension must verify the identity of the economic actor, not the intermediary. Two sound approaches:

1. **Forward the original caller via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to populate the field honestly, which is acceptable given it is a protocol-controlled contract.

2. **Check `sender` against the router and fall back to an inner-caller field**: If `sender == router`, require a verified inner-caller address from `extensionData`; otherwise check `sender` directly.

The simplest correct fix is to have `MetricOmmSimpleRouter` always append the original `msg.sender` to `extensionData` and have `SwapAllowlistExtension` decode it when `sender` is a known router address.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as BEFORE_SWAP extension.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
   → allowedSwapper[pool][alice] = true
3. Pool admin calls setAllowedToSwap(pool, router, true)
   (required so Alice can use the router).
   → allowedSwapper[pool][router] = true

Attack
──────
4. Bob (not allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({
         pool:      pool,
         recipient: bob,
         zeroForOne: true,
         amountIn:  X,
         ...
     })

5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension.beforeSwap checks:
     allowedSwapper[pool][router]  →  true   ✓

8. Swap executes. Bob receives output tokens.
   The allowlist guard was never consulted for Bob's identity.
```

Bob, an address the pool admin explicitly did not allowlist, successfully swaps against a pool that was supposed to be restricted.