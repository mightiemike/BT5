Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling full allowlist bypass via the periphery router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` on the pool — the router contract when a user enters through `MetricOmmSimpleRouter`. When a pool admin allowlists the router to support router-mediated swaps for curated users, every unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`, because the extension cannot distinguish which end-user the router is acting for.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards this value unchanged to the extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```

The router does not forward the original `msg.sender` (the end-user) anywhere in the swap call. The pool sees `msg.sender == router`, so `sender == router` is what reaches the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

Two broken states result:
1. **Allowlist blocks legitimate users**: If the admin allowlists individual KYC'd users but not the router, those users cannot swap through the router even though they are explicitly permitted.
2. **Allowlist bypass**: If the admin allowlists the router (the natural operational step to enable router-mediated swaps for permitted users), every user — including those not on the allowlist — can bypass the gate by routing through `MetricOmmSimpleRouter`.

`DepositAllowlistExtension.beforeAddLiquidity` avoids this problem by checking `owner` (the position owner, always the intended economic actor regardless of who calls `addLiquidity`), but `SwapAllowlistExtension` has no equivalent indirection — there is no "swap owner" field in the swap call path.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., for regulatory compliance, to prevent toxic arbitrage flow, or to gate a private market) loses that protection entirely once the router is allowlisted. Any unprivileged user can execute swaps on the curated pool by routing through `MetricOmmSimpleRouter`, draining LP value through arbitrage or violating the pool's access policy. This is an admin-boundary break: the pool admin's restriction is bypassed by an unprivileged path.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router. This is the natural operational step any admin would take when deploying a curated pool expected to be used through the standard periphery. The admin has no indication from the extension's interface or documentation that allowlisting the router opens the gate to all users. Likelihood is medium-high for any production curated pool that supports router access.

## Recommendation
The extension must identify the actual end-user, not the immediate caller. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Check `recipient` instead of `sender`**: The recipient is the address that receives output tokens and is the economically relevant actor. The pool already passes `recipient` as the second argument to `beforeSwap`. This mirrors the `DepositAllowlistExtension` pattern of checking the economic actor (`owner`) rather than the immediate caller (`sender`).

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension E
  admin allowlists user A:   allowedSwapper[P][A] = true
  admin allowlists router R: allowedSwapper[P][R] = true
    (natural step to let A use the router)

Attack:
  user B (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: P, recipient: B, ...})

  Router calls:
    P.swap(B, ...)   // msg.sender on pool = router R

  Pool calls (MetricOmmPool.sol L230-231):
    _beforeSwap(sender=R, recipient=B, ...)

  Extension checks (SwapAllowlistExtension.sol L37):
    allowedSwapper[P][R] == true  →  passes

  Result: B's swap executes on the curated pool, bypassing the allowlist.
```