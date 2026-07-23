### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of the pool's `swap` function — the router — not the end user. When a pool admin allowlists the `MetricOmmSimpleRouter` to support router-mediated swaps, every user on the network can bypass the per-user allowlist by routing through that public contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool via `callExtension`), and `sender` is the first argument forwarded by the pool's `_beforeSwap` dispatcher:

```solidity
function _beforeSwap(
    address sender,
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

The pool's `swap` function takes `recipient` as an explicit parameter but uses `msg.sender` as `sender` (confirmed by `simulateSwapAndRevert(users[0], false, ...)` having only one address argument — the recipient — while `_beforeSwap` has two address slots). When `MetricOmmSimpleRouter` calls `pool.swap(recipient=user, ...)`, the pool records `sender = address(router)`.

The allowlist lookup therefore becomes:

```
allowedSwapper[pool][router]   // NOT allowedSwapper[pool][user]
```

A pool admin who wants router-mediated swaps to work for their allowlisted users **must** add the router to the allowlist. The moment they do, `allowedSwapper[pool][router] == true` for every pool that uses the router, and any address on the network can call `MetricOmmSimpleRouter.exactInputSingle(...)` and pass the check — the per-user gate is completely neutralised.

The `SwapAllowlistExtension` mapping is keyed correctly by pool:

```solidity
mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
``` [3](#0-2) 

but the inner key is always the router, never the human swapper, so the per-user granularity is structurally unreachable through the router path.

---

### Impact Explanation

**Severity: High**

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any privileged role; they only need to call the public router. Consequences include:

- Unrestricted token swaps against a pool whose admin believed access was gated.
- Potential drain of pool liquidity by actors the pool admin explicitly excluded.
- Protocol-fee revenue and LP principal exposed to actors outside the intended participant set.

---

### Likelihood Explanation

**Likelihood: High**

- `MetricOmmSimpleRouter` is a public, permissionless periphery contract.
- Any pool admin who enables router-based swaps for their allowlisted users must add the router to the allowlist, which simultaneously opens the gate to everyone.
- No special knowledge, flash loan, or multi-step setup is required — a single `exactInputSingle` call suffices.
- The bypass is deterministic and repeatable on every block.

---

### Recommendation

The allowlist must be keyed on the **end user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the originating user through the router.** Have `MetricOmmSimpleRouter` pass `msg.sender` (the human caller) as the `sender` argument to `pool.swap`, so the pool forwards the real user identity to the extension. This requires the pool's `swap` function to accept an explicit `sender` parameter rather than using `msg.sender`.

2. **Gate on `recipient` as a fallback.** If the pool architecture cannot expose the originating user as `sender`, the extension should check `recipient` (the address that receives output tokens) as the economically relevant identity, since the router always sets `recipient = msg.sender` (the user).

Either way, the extension's check must resolve to the human address that benefits from the swap, not the intermediary contract that relays it.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin allowlists only address Alice: allowedSwapper[P][Alice] = true
  pool admin also allowlists the router (required for router swaps): allowedSwapper[P][router] = true

Attack (Bob, not allowlisted):
  Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, recipient: Bob, ...})
  Router calls P.swap(recipient=Bob, ...)
  Pool sets sender = address(router), calls E.beforeSwap(sender=router, ...)
  E checks: allowedSwapper[P][router] == true  → passes
  Bob's swap executes against the allowlisted pool
  Pool admin's per-user gate is bypassed
```

The check `allowedSwapper[msg.sender][sender]` at line 37 of `SwapAllowlistExtension.sol` resolves to `allowedSwapper[pool][router]` — a single boolean that unlocks the gate for the entire user population. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
