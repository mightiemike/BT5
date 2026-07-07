### Title
Stale Sequencer-Fed Prices Used in Slow Mode `WithdrawCollateral` Health Check — (`core/contracts/Endpoint.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

The `priceX18` mapping in `EndpointStorage` stores asset prices with no associated timestamp and no staleness guard. Prices are exclusively updated by the sequencer via `UpdatePrice` transactions. The protocol explicitly handles sequencer downtime through a slow mode path, but the slow mode `WithdrawCollateral` execution path performs a health check using these potentially stale prices. An unprivileged user can exploit this to withdraw collateral against a stale (inflated) price, leaving the protocol undercollateralized.

---

### Finding Description

**Root cause — no timestamp or staleness check on `priceX18`:**

`EndpointStorage.sol` declares the price mapping as a plain value with no update timestamp:

```solidity
mapping(uint32 => int128) internal priceX18;
``` [1](#0-0) 

`getPriceX18()` in `Endpoint.sol` returns the stored value with no age validation:

```solidity
function getPriceX18(uint32 productId) public override returns (int128 _priceX18) {
    _priceX18 = priceX18[productId];
    require(_priceX18 != 0, ERR_INVALID_PRODUCT);
    emit PriceQuery(productId);
}
``` [2](#0-1) 

Prices are updated only through the sequencer-gated `UpdatePrice` transaction path:

```solidity
} else if (txType == IEndpoint.TransactionType.UpdatePrice) {
    (uint32 productId, int128 newPriceX18) = clearinghouse.updatePrice(transaction);
    if (productId != 0) {
        priceX18[productId] = newPriceX18;
    }
``` [3](#0-2) 

`submitTransactionsChecked` enforces `msg.sender == sequencer`, so no unprivileged caller can push a fresh price. When the sequencer is offline, `priceX18` freezes at its last value indefinitely. [4](#0-3) 

**Attacker-reachable path — slow mode `WithdrawCollateral`:**

The protocol explicitly provides a censorship-resistance path. Any user can submit a `WithdrawCollateral` slow mode transaction:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [5](#0-4) 

After `SLOW_MODE_TX_DELAY` (3 days), anyone can call `executeSlowModeTransaction()` with no access control:

```solidity
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);
``` [6](#0-5) 

The slow mode `WithdrawCollateral` handler calls `clearinghouse.withdrawCollateral()`:

```solidity
clearinghouse.withdrawCollateral(
    txn.sender, txn.productId, txn.amount, address(0), nSubmissions
);
``` [7](#0-6) 

**Health check uses stale price:**

`withdrawCollateral` in `Clearinghouse.sol` performs a health check after debiting the balance:

```solidity
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [8](#0-7) 

`getHealth()` calls `spotEngine.getHealthContribution()` and `perpEngine.getHealthContribution()`, which use engine-internal prices updated by `engine.updatePrice()` — also exclusively sequencer-fed via `Clearinghouse.updatePrice()`:

```solidity
function updatePrice(bytes calldata transaction) external onlyEndpoint returns (uint32, int128) {
    ...
    engine.updatePrice(txn.productId, txn.priceX18);
    return (txn.productId, txn.priceX18);
``` [9](#0-8) 

Both price stores (`priceX18` in `EndpointStorage` and engine-internal prices) are frozen when the sequencer is down. Neither has a staleness guard.

---

### Impact Explanation

During sequencer downtime, a user holding a volatile collateral asset whose market price has dropped can:

1. Submit a slow mode `WithdrawCollateral` for the maximum amount their stale-price health allows.
2. Wait 3 days for the slow mode delay to expire.
3. Execute the transaction — the health check passes because it uses the frozen (pre-drop) price.
4. Withdraw collateral at the stale inflated valuation, leaving the protocol holding an undercollateralized subaccount.

The corrupted state is the subaccount's collateral balance: the user extracts more real tokens than their actual current health permits. The protocol's insurance fund and other users bear the resulting shortfall.

---

### Likelihood Explanation

Sequencer downtime is a realistic, non-attacker-controlled scenario that the protocol explicitly anticipates — the entire slow mode system exists for this case. The 3-day delay window provides ample time for significant price movement in volatile assets. No special privileges, key compromise, or social engineering are required; any depositor can submit a slow mode withdrawal.

---

### Recommendation

1. **Record a price timestamp alongside each update.** Add a `uint128 lastUpdatedAt` field next to `priceX18` and in engine price storage.
2. **Enforce a staleness bound before executing slow mode withdrawals.** If `block.timestamp - lastUpdatedAt > MAX_PRICE_AGE`, revert or pause the withdrawal.
3. **Integrate a fallback on-chain oracle** (e.g., Chainlink) that can be queried when sequencer-fed prices are stale, mirroring the dual-oracle pattern common in lending protocols.

---

### Proof of Concept

1. Sequencer goes offline. `priceX18[tokenX]` is frozen at $100 (last sequencer update).
2. Token X's market price falls to $60 over the next 24 hours.
3. Attacker (who holds Token X as collateral) calls `submitSlowModeTransaction` with a `WithdrawCollateral` transaction requesting the maximum amount their $100-priced health allows.
4. Three days pass. Sequencer remains offline. Token X is now at $50.
5. Attacker calls `executeSlowModeTransaction()` (permissionless).
6. `clearinghouse.withdrawCollateral()` debits the balance, then calls `getHealth()`.
7. `getHealth()` uses the stale engine price of $100 — health check passes.
8. Attacker receives tokens worth $50 each, valued at $100 each by the protocol.
9. The subaccount is now undercollateralized at real market prices; the protocol absorbs the loss.

### Citations

**File:** core/contracts/EndpointStorage.sol (L60-60)
```text
    mapping(uint32 => int128) internal priceX18;
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```

**File:** core/contracts/Endpoint.sol (L334-342)
```text
    function getPriceX18(uint32 productId)
        public
        override
        returns (int128 _priceX18)
    {
        _priceX18 = priceX18[productId];
        require(_priceX18 != 0, ERR_INVALID_PRODUCT);
        emit PriceQuery(productId);
    }
```

**File:** core/contracts/EndpointTx.sol (L222-229)
```text
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L486-492)
```text
        } else if (txType == IEndpoint.TransactionType.UpdatePrice) {
            (uint32 productId, int128 newPriceX18) = clearinghouse.updatePrice(
                transaction
            );
            if (productId != 0) {
                priceX18[productId] = newPriceX18;
            }
```

**File:** core/contracts/Clearinghouse.sol (L358-375)
```text
    function updatePrice(bytes calldata transaction)
        external
        onlyEndpoint
        returns (uint32, int128)
    {
        IEndpoint.UpdatePrice memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.UpdatePrice)
        );
        require(txn.priceX18 > 0, ERR_INVALID_PRICE);
        IProductEngine engine = productToEngine[txn.productId];
        if (address(engine) != address(0)) {
            engine.updatePrice(txn.productId, txn.priceX18);
            return (txn.productId, txn.priceX18);
        } else {
            return (0, 0);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L419-419)
```text
        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
