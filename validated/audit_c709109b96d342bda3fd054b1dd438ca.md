Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of the `addLiquidity` call) and instead gates access on `owner` (the LP position recipient), which is a freely caller-supplied argument independent of `msg.sender`. Because `addLiquidity` explicitly supports an operator pattern where `msg.sender != owner`, any unprivileged address can bypass the allowlist by passing any known allowlisted address as `owner`. The deposit allowlist is rendered completely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` accepts `owner` as a caller-supplied argument with no requirement that it equals `msg.sender`:

```solidity
// MetricOmmPool.sol L182-195
function addLiquidity(
    address owner,   // freely chosen by caller
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    ...
}
```

The NatSpec for `addLiquidity` explicitly documents this: *"`msg.sender` pays but need not equal `owner` (operator pattern)"* — confirming the separation is by design.

`ExtensionCalling._beforeAddLiquidity` passes both `sender` (`msg.sender`) and `owner` (position recipient) to the extension hook:

```solidity
// ExtensionCalling.sol L88-99
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
```

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` (unnamed first parameter) and checks `owner` instead:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender`:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Exploit path:
1. Pool admin allowlists `alice`: `setAllowedToDeposit(pool, alice, true)`.
2. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
3. Pool calls `_beforeAddLiquidity(sender=bob, owner=alice, ...)`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `bob`'s tokens are pulled via the modify-liquidity callback; LP position is minted to `alice`.
6. `bob` has deposited into a restricted pool without being allowlisted. The check `allowedDepositor[pool][bob]` is never evaluated.

No existing guard prevents this: `addLiquidity` has no `owner == msg.sender` requirement (unlike `removeLiquidity` which enforces `if (msg.sender != owner) revert NotPositionOwner()`).

## Impact Explanation
The deposit allowlist — the sole access control mechanism for restricted pools — is completely bypassed. Any unprivileged address can inject liquidity into pools intended for KYC/institutional-only access. Consequences include: unauthorized liquidity injection altering bin distribution and fee dilution for existing LPs; allowlisted addresses receiving unsolicited LP positions that alter their exposure; and complete loss of the KYC/access-control guarantee for restricted pools. This constitutes a broken core pool functionality (access control) causing direct impact to LP assets and pool integrity, meeting the High severity threshold.

## Likelihood Explanation
Exploitation requires only a standard `addLiquidity` call with `owner` set to any known allowlisted address (e.g., the pool admin or any existing LP, both discoverable on-chain). No privileged access, flash loans, special tokens, or malicious setup is required. The attack is repeatable at will by any address. Likelihood is **High**.

## Recommendation
Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Pool admins should then allowlist the actual depositor addresses (e.g., the router contract or individual LP addresses acting as `msg.sender`), not position-owner addresses.

## Proof of Concept
1. Deploy a pool with `DepositAllowlistExtension` configured in `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is authorized.
3. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `bob`'s tokens are pulled via callback; LP position is minted to `alice`.
6. `bob` has successfully deposited into a restricted pool. `allowedDepositor[pool][bob]` was never checked.

Foundry test: deploy pool + extension, configure allowlist for `alice` only, prank as `bob`, call `addLiquidity` with `owner = alice`, assert no revert and that `bob`'s token balance decreased.