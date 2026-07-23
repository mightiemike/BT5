### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the pool's `msg.sender` at swap time. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router (the only way to let any user swap through it), the allowlist gate is silently open to every address on-chain.

---

### Finding Description

`SwapAllowlistExtension` is designed to restrict which addresses may swap in a given pool: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct key for the per-pool mapping), and `sender` is the first argument forwarded by the pool's `_beforeSwap` dispatcher: [2](#0-1) 

The pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`. When the call originates from `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...)          // msg.sender in pool = router
     → _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router]   ← wrong identity
```

A pool admin who wants to allow router-mediated swaps must add the router to the allowlist. Once the router is allowlisted, **every** address on-chain can bypass the per-user gate simply by calling `MetricOmmSimpleRouter` instead of the pool directly.

The analog to the external report is exact: just as `StandardPolicyERC1155` hardcodes `1` instead of `order.amount` — substituting a wrong fixed value for the intended configured value — `SwapAllowlistExtension` substitutes the router address for the intended user address, making the configured guard structurally inoperative for router-mediated swaps.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers). Once the router is allowlisted — which is required for any allowlisted user to swap through the official periphery — the restriction is fully bypassed. Any non-allowlisted address can drain liquidity from the restricted pool at oracle-quoted prices, causing direct loss of LP principal to pool owners who believed access was gated.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user can call `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant) targeting a pool that has the extension active and the router allowlisted. The router is a public, permissionless contract. The pool admin enabling the allowlist and adding the router is the expected operational path, making the bypass reachable in every realistic deployment of this extension.

---

### Recommendation

The extension must check the **originating user**, not the intermediary caller. Two sound approaches:

1. **Pass the user through the router**: Have `MetricOmmSimpleRouter` encode the real `msg.sender` into `extensionData` and have the extension decode it — but this requires the pool to authenticate the router as a trusted forwarder, which introduces its own complexity.

2. **Check `recipient` or a dedicated field**: Redesign the hook signature so the pool passes the economic actor (the address that will receive tokens or pay tokens) rather than the syntactic caller. The `recipient` argument is already available in `beforeSwap` and represents the end-user in a direct swap.

3. **Simplest fix**: In `SwapAllowlistExtension.beforeSwap`, check both `sender` and `recipient` — or require that the pool admin allowlist users, not routers, and document that the extension is incompatible with router-mediated flows until a trusted-forwarder pattern is implemented.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension active.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so users can swap
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(alice, ...)
  - Pool calls _beforeSwap(sender=router, recipient=alice, ...)
  - Extension checks allowedSwapper[pool][router] → true  ✓ (passes)
  - alice's swap executes at oracle price, draining pool liquidity

Expected: revert NotAllowedToSwap (alice is not allowlisted)
Actual:   swap succeeds (router is allowlisted, alice is never checked)
``` [3](#0-2) [4](#0-3) [5](#0-4)

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
