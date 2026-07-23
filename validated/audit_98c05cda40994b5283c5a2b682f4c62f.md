Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the `pool.swap()` call — the router contract, not the originating EOA. Because `MetricOmmPool.swap` unconditionally invokes `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` to settle the input token, EOAs cannot call `pool.swap()` directly and must route through `MetricOmmSimpleRouter`. Any pool admin who allowlists the router to enable EOA trading silently opens the pool to every user on-chain, completely defeating the allowlist.

## Finding Description

**Root cause — wrong actor bound in the extension check.**

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (correct — only authorized caller of the extension). `sender` is the first argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call.

**Pool side — `sender` is always the direct caller of `swap()`:**

In `MetricOmmPool.swap`, `msg.sender` (the direct caller) is passed as the first argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` then forwards this as the first argument to `IMetricOmmExtensions.beforeSwap`.

**Router side — the router is the direct caller of `pool.swap()`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So `msg.sender` inside the pool is the **router address**. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Why EOAs cannot bypass the router:**

`MetricOmmPool.swap` unconditionally calls:

```solidity
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
```

EOAs have no code and cannot implement this callback, so they **must** go through the router or another intermediary contract.

**The two failure modes:**

| Pool admin action | Outcome |
|---|---|
| Allowlists individual user addresses only | Router swaps revert for everyone (router not allowlisted); pool is unusable for EOAs |
| Allowlists the router to make the pool usable | Every user on-chain can swap; allowlist is completely bypassed |

There is no configuration that simultaneously (a) allows EOA users to swap via the router and (b) restricts access to a curated set of users.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market participants, or protocol-internal actors) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker needs no special access — they simply call `exactInputSingle` or `exactInput` on the public router. All pool liquidity is exposed to unauthorized swappers, violating the core invariant that curated pools enforce access control on the economically relevant actor. This constitutes broken core pool functionality causing unauthorized access to pool assets, meeting the High severity threshold.

## Likelihood Explanation

The router is the primary and intended user-facing swap interface. Pool admins who deploy a `SwapAllowlistExtension` and want their allowlisted users to be able to trade will naturally allowlist the router (or discover that without doing so, no EOA can swap at all). The bypass requires no privileged access, no special timing, and no complex setup — any user can call the public router functions. The precondition (router allowlisted) is the expected operational state for any pool using this extension with EOA users.

## Recommendation

The extension must identify the **originating user**, not the intermediary. Two complementary approaches:

1. **Trusted-router pattern with `extensionData`:** The extension first asserts `sender` (the direct pool caller) is a known trusted router, then reads the real user from a signed payload or trusted-forwarder encoding in `extensionData`. This requires the extension to maintain a router allowlist in addition to the swapper allowlist.

2. **ERC-2771 trusted forwarder semantics:** Deploy a thin per-user proxy or use a trusted forwarder so the pool always sees the real user as `msg.sender`, eliminating the intermediary identity problem entirely.

3. **Restrict to direct pool callers only:** If the pool is intended for contract-to-contract use only (no EOA routing), document and enforce that the allowlist covers only direct callers, and do not allowlist the public router.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (required so that any EOA can swap at all)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (EOA, not allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams({
          pool: pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      }))

Execution trace:
  router.exactInputSingle()
    → pool.swap(msg.sender=router, ...)
        → _beforeSwap(sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → allowedSwapper[pool][router] == true  ✓ (passes)
        → swap executes, attacker receives output tokens

Result:
  attacker successfully swaps on a pool that was supposed to block them.
  The allowlist check passed because it verified the router's address,
  not the attacker's address.
```