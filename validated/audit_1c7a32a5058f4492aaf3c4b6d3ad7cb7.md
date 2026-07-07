### Title
Slow Mode Fees Permanently Locked in Endpoint Contract Due to Missing Collection Mechanism — (`core/contracts/EndpointStorage.sol` / `core/contracts/EndpointTx.sol`)

---

### Summary

The Nado protocol charges users a slow mode fee for every user-submitted slow mode transaction. The ERC20 tokens are transferred into the `Endpoint` contract itself, and a tracking variable `slowModeFees` is incremented. However, no on-chain mechanism exists to claim, redistribute, or credit these tokens to any protocol-controlled account. The tokens accumulate permanently in the `Endpoint` contract with no recovery path visible in the production code.

---

### Finding Description

In `EndpointTx.submitSlowModeTransactionImpl`, every non-owner slow mode transaction (e.g., `WithdrawCollateral`, `LinkSigner`, `ClaimBuilderFee`) triggers:

```solidity
chargeSlowModeFee(_getQuote(), sender);
slowModeFees += SLOW_MODE_FEE;
``` [1](#0-0) 

`chargeSlowModeFee` is defined in `EndpointStorage.sol` as:

```solidity
function chargeSlowModeFee(IERC20Base token, address from) internal virtual {
    require(address(token) != address(0));
    token.safeTransferFrom(from, address(this), clearinghouse.getSlowModeFee());
}
``` [2](#0-1) 

The ERC20 tokens are transferred to `address(this)` — the `Endpoint` contract — not to the `Clearinghouse` or any engine account. The `slowModeFees` state variable is declared as:

```solidity
int128 internal slowModeFees;
``` [3](#0-2) 

This variable is **only ever written to** (incremented) and is never read by any function in the production contract code. It is never referenced in the `DumpFees` processing path.

The `DumpFees` slow mode transaction — the protocol's only fee-collection mechanism — handles two things:

1. Trading fees via `IOffchainExchange.dumpFees()` → credits `X_ACCOUNT` in the engine.
2. Sequencer fees via `clearinghouse.claimSequencerFees(fees)` → credits `X_ACCOUNT` in the engine. [4](#0-3) 

Neither step touches the ERC20 balance sitting in the `Endpoint` contract from slow mode fees. The `sequencerFee` mapping (the active per-product fee tracker used by `chargeFee`) is entirely separate from `slowModeFees`:

```solidity
mapping(uint32 => int128) internal sequencerFee;
``` [5](#0-4) 

`chargeFee` credits `sequencerFee[productId]` and deducts from the user's engine balance — this is properly collected. But `chargeSlowModeFee` transfers real ERC20 tokens to the `Endpoint` contract and only increments the dead `slowModeFees` variable. There is no analogous collection path for these tokens.

Additionally, `clearinghouse.getSlowModeFee()` returns a decimal-adjusted amount:

```solidity
int256 multiplier = int256(10**(token.decimals() - 6));
return uint256(int256(SLOW_MODE_FEE) * multiplier);
``` [6](#0-5) 

While `slowModeFees += SLOW_MODE_FEE` adds only the raw constant `1000000`, meaning even the tracking variable does not accurately reflect the actual ERC20 amount locked.

---

### Impact Explanation

Every slow mode transaction submitted by a non-owner user (withdraw collateral, link signer, claim builder fee, etc.) permanently locks `SLOW_MODE_FEE` worth of quote tokens ($1 in USDC terms, adjusted for decimals) in the `Endpoint` contract. These tokens are never credited to `X_ACCOUNT`, `FEES_ACCOUNT`, insurance, or any other protocol-controlled destination. The protocol loses all slow mode fee revenue. Over the lifetime of the protocol, this constitutes a continuous, compounding loss of protocol revenue with no recovery path in the on-chain code.

---

### Likelihood Explanation

High. Every user who submits a slow mode transaction triggers this path. Slow mode transactions are a standard user-facing flow (withdrawals, signer linking, builder fee claims). The fee is charged unconditionally for all non-owner transaction types. No special conditions or attacker knowledge are required — any ordinary user interaction causes the loss.

---

### Recommendation

The `DumpFees` processing path in `EndpointTx.processSlowModeTransactionImpl` should be extended to also transfer the accumulated ERC20 slow mode fees from the `Endpoint` contract to the `Clearinghouse` (or directly credit `X_ACCOUNT` in the spot engine). Specifically:

1. Replace the dead `slowModeFees` tracking variable with a per-product mapping consistent with `sequencerFee`, or track the actual ERC20 amount.
2. In the `DumpFees` handler, transfer the accumulated ERC20 balance from the `Endpoint` contract to the `Clearinghouse` and credit it to `X_ACCOUNT` via `spotEngine.updateBalance`, consistent with how sequencer fees are handled.

---

### Proof of Concept

1. User calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateral` payload.
2. `submitSlowModeTransactionImpl` executes the `else` branch: `chargeSlowModeFee(_getQuote(), sender)` transfers `getSlowModeFee()` USDC from the user to the `Endpoint` contract address; `slowModeFees += SLOW_MODE_FEE` increments the dead variable.
3. Owner later calls `ContractOwner.dumpFees()` → `Endpoint.submitSlowModeTransaction(DumpFees)` → `processSlowModeTransactionImpl` handles `DumpFees`: calls `offchainExchange.dumpFees()` and `clearinghouse.claimSequencerFees(fees)`. Neither touches the ERC20 balance in the `Endpoint` contract.
4. The USDC from step 2 remains permanently in the `Endpoint` contract. `slowModeFees` is incremented but never read or acted upon. The protocol receives no credit for the fee. [4](#0-3) [2](#0-1)

### Citations

**File:** core/contracts/EndpointTx.sol (L244-253)
```text
        } else if (txType == IEndpoint.TransactionType.DumpFees) {
            IOffchainExchange(offchainExchange).dumpFees();
            uint32[] memory spotIds = spotEngine.getProductIds();
            int128[] memory fees = new int128[](spotIds.length);
            for (uint256 i = 0; i < spotIds.length; i++) {
                fees[i] = sequencerFee[spotIds[i]];
                sequencerFee[spotIds[i]] = 0;
            }
            requireSubaccount(X_ACCOUNT);
            clearinghouse.claimSequencerFees(fees);
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointStorage.sol (L48-48)
```text
    mapping(uint32 => int128) internal sequencerFee;
```

**File:** core/contracts/EndpointStorage.sol (L55-55)
```text
    int128 internal slowModeFees;
```

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L763-765)
```text
        );
        int256 multiplier = int256(10**(token.decimals() - 6));
        return uint256(int256(SLOW_MODE_FEE) * multiplier);
```
