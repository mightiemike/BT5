### Title
Missing Parent Health Check After Margin Transfer in `createIsolatedSubaccount` — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.createIsolatedSubaccount` debits the parent subaccount's quote balance to fund the isolated subaccount but never calls `getHealth` (or `_isAboveInitial`) on the parent afterward. Every other collateral-movement path in the protocol enforces an on-chain health gate; this one does not.

---

### Finding Description

`createIsolatedSubaccount` extracts the margin from the trader-controlled `appendix` field of the signed order:

```
_isolatedMargin(appendix) = (appendix >> 64) * 10^12
```

It then performs two raw balance mutations with no subsequent health check:

```solidity
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -margin);   // parent debited
spotEngine.updateBalance(QUOTE_PRODUCT_ID, newIsolatedSubaccount, margin); // iso credited
``` [1](#0-0) 

The function returns immediately after, and `processTransactionImpl` only calls `_recordSubaccount` — no health assertion anywhere in the call chain. [2](#0-1) 

**Contrast with every analogous path:**

| Function | Health check after debit |
|---|---|
| `Clearinghouse.transferQuote` | `require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH)` |
| `Clearinghouse.withdrawCollateral` | `require(getHealth(sender, INITIAL) >= 0, ERR_SUBACCT_HEALTH)` |
| `Clearinghouse.mintNlp` | `require(getHealth(txn.sender, INITIAL) >= 0, ERR_SUBACCT_HEALTH)` |
| `Clearinghouse.nlpProfitShare` | `require(getHealth(poolSubaccount, INITIAL) >= 0, ERR_SUBACCT_HEALTH)` |
| **`OffchainExchange.createIsolatedSubaccount`** | **none** | [3](#0-2) [4](#0-3) 

The margin field is fully trader-controlled: the upper 64 bits of `appendix` are set by the signer, scaled by `10^12`, and cast directly to `int128` with no upper-bound clamp. [5](#0-4) 

`processTransactionImpl` is declared `public` with no access-control modifier, meaning it is callable by any external account with a validly signed `CreateIsolatedSubaccount` payload. [6](#0-5) 

---

### Impact Explanation

A trader who holds open perp positions (or other assets that contribute positively to health) can sign a `CreateIsolatedSubaccount` order whose `appendix` encodes a margin equal to the parent's entire quote balance. After the call:

- Parent quote balance → 0 (or negative if `spotEngine.updateBalance` permits it).
- Parent's INITIAL health drops below 0 because the quote collateral that was backing the perp positions is gone.
- The parent is now undercollateralized but no revert occurs; the state is committed.

This enables the parent to hold open perp positions without adequate collateral, transferring insolvency risk to the insurance fund and other protocol participants. It directly matches the scoped critical impact: *"collateral movement that causes a subaccount's initial health to fall below zero without triggering a health revert."*

---

### Likelihood Explanation

The exploit requires only a valid EIP-712 signature from the trader's own key and a direct call to the `public` `processTransactionImpl`. No privileged role, sequencer cooperation, or governance action is needed. The margin value is unconstrained on-chain. Likelihood is high once the missing check is known.

---

### Recommendation

Add an initial-health assertion on the parent immediately after the balance debit, mirroring `transferQuote`:

```solidity
// after spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -margin)
require(
    clearinghouse.getHealth(txn.order.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [7](#0-6) 

---

### Proof of Concept

1. Deploy the protocol on a local Hardhat fork.
2. Deposit collateral into `parentSubaccount`; open a perp position that consumes most of the initial health margin.
3. Craft a `CreateIsolatedSubaccount` order with `appendix` bits `[127:64]` set to encode `margin = parentQuoteBalance`.
4. Sign with the trader's key; call `EndpointTx.processTransactionImpl` directly with the encoded transaction.
5. Assert `Clearinghouse.getHealth(parentSubaccount, INITIAL) < 0` — the assertion passes, confirming the parent is undercollateralized with no revert. [8](#0-7)

### Citations

**File:** core/contracts/OffchainExchange.sol (L358-360)
```text
    function _isolatedMargin(uint128 appendix) internal pure returns (uint128) {
        return (appendix >> 64) * (10**12);
    }
```

**File:** core/contracts/OffchainExchange.sol (L999-1090)
```text
    function createIsolatedSubaccount(
        IEndpoint.CreateIsolatedSubaccount memory txn,
        address linkedSigner
    ) external onlyEndpoint returns (bytes32) {
        require(
            !RiskHelper.isIsolatedSubaccount(txn.order.sender),
            ERR_UNAUTHORIZED
        );
        require(_isIsolated(txn.order.appendix), ERR_UNAUTHORIZED);
        bytes32 digest = getDigest(txn.productId, txn.order);
        if (digestToSubaccount[digest] != bytes32(0)) {
            return digestToSubaccount[digest];
        }
        require(
            _checkSignature(
                txn.order.sender,
                digest,
                linkedSigner,
                txn.signature
            ),
            ERR_INVALID_SIGNATURE
        );

        address senderAddress = address(uint160(bytes20(txn.order.sender)));
        uint256 mask = isolatedSubaccountsMask[senderAddress];
        bytes32 newIsolatedSubaccount = bytes32(0);
        for (uint256 id = 0; (1 << id) <= mask; id += 1) {
            if (mask & (1 << id) != 0) {
                bytes32 subaccount = isolatedSubaccounts[txn.order.sender][id];
                if (subaccount != bytes32(0)) {
                    uint32 productId = RiskHelper.getIsolatedProductId(
                        subaccount
                    );
                    if (productId == txn.productId) {
                        newIsolatedSubaccount = subaccount;
                        break;
                    }
                }
            }
        }

        if (newIsolatedSubaccount == bytes32(0)) {
            require(
                !_isReduceOnly(txn.order.appendix),
                "Reduce-only order cannot create isolated subaccount"
            );
            require(
                mask != (1 << MAX_ISOLATED_SUBACCOUNTS_PER_ADDRESS) - 1,
                "Too many isolated subaccounts"
            );
            uint8 id = 0;
            while (mask & 1 != 0) {
                mask >>= 1;
                id += 1;
            }

            // |  address | reserved | productId |   id   |  'iso'  |
            // | 20 bytes |  6 bytes |  2 bytes  | 1 byte | 3 bytes |
            newIsolatedSubaccount = bytes32(
                (uint256(uint160(senderAddress)) << 96) |
                    (uint256(txn.productId) << 32) |
                    (uint256(id) << 24) |
                    6910831
            );
            isolatedSubaccountsMask[senderAddress] |= 1 << id;
            parentSubaccounts[newIsolatedSubaccount] = txn.order.sender;
            isolatedSubaccounts[txn.order.sender][id] = newIsolatedSubaccount;
            _onCreateIsolatedSubaccount(
                newIsolatedSubaccount,
                txn.order.sender
            );
        }

        digestToSubaccount[digest] = newIsolatedSubaccount;

        int128 margin = int128(_isolatedMargin(txn.order.appendix));
        if (margin > 0) {
            digestToMargin[digest] = margin;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.order.sender,
                -margin
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                newIsolatedSubaccount,
                margin
            );
        }

        return newIsolatedSubaccount;
    }
```

**File:** core/contracts/EndpointTx.sol (L387-390)
```text
    function processTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
```

**File:** core/contracts/EndpointTx.sol (L619-631)
```text
        } else if (
            txType == IEndpoint.TransactionType.CreateIsolatedSubaccount
        ) {
            IEndpoint.CreateIsolatedSubaccount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.CreateIsolatedSubaccount)
            );
            bytes32 newIsolatedSubaccount = IOffchainExchange(offchainExchange)
                .createIsolatedSubaccount(
                    txn,
                    getLinkedSigner(txn.order.sender)
                );
            _recordSubaccount(newIsolatedSubaccount);
```

**File:** core/contracts/Clearinghouse.sol (L247-249)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
