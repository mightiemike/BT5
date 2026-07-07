### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Drain from DirectDepositV1 Contracts — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` is a publicly callable function (no access control) that performs a token swap: it pulls USDC from `msg.sender` into a `DirectDepositV1` contract, then withdraws usdcE from that contract and sends it to `msg.sender`. The USDC `transferFrom` call's return value is never checked. If USDC on Ink chain returns `false` on failure rather than reverting, the function silently skips the USDC pull but still executes the usdcE withdrawal and transfer, allowing any caller to drain usdcE from any `DirectDepositV1` contract for free.

---

### Finding Description

`replaceUsdcEWithUsdc` is defined in `ContractOwner.sol` with only a chain-ID guard and no ownership check: [1](#0-0) 

The critical sequence is:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // return value ignored
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));       // sends usdcE → ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);                 // sends usdcE → msg.sender
``` [2](#0-1) 

`IERC20Base.transferFrom` is called directly on the raw interface — not through `ERC20Helper.safeTransferFrom`, which wraps the call and asserts success: [3](#0-2) 

By contrast, the outbound usdcE transfer on line 618 correctly uses `safeTransfer` (via `using ERC20Helper for IERC20Base`), but the inbound USDC pull does not. [4](#0-3) 

`DirectDepositV1.withdraw` is `onlyOwner`, and `ContractOwner` is the owner, so step 2 always succeeds regardless of whether step 1 transferred anything. [5](#0-4) 

---

### Impact Explanation

**Impact: High.**

Any `DirectDepositV1` contract that still holds a usdcE balance (the exact scenario this migration function targets) can be fully drained. The attacker receives usdcE without providing any USDC. The corrupted asset delta is the entire usdcE balance of the targeted `DirectDepositV1`, which belongs to the subaccount owner. The `directDepositV1Address` mapping is public, so all eligible targets are enumerable on-chain. [6](#0-5) 

---

### Likelihood Explanation

**Likelihood: Medium.**

The exploitability depends on whether the USDC token at the hardcoded address on Ink chain (chainid 57073) returns `false` on a failed `transferFrom` rather than reverting. Many bridged or wrapped USDC variants (e.g., USDC.e) follow the older ERC20 pattern of returning `false` instead of reverting. The function is permissionless — any EOA or contract can call it — and the migration context means `DirectDepositV1` contracts with usdcE balances are the intended targets, so live funds are at risk. [7](#0-6) 

---

### Recommendation

Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom`, which asserts the return value:

```diff
- IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
+ IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`safeTransferFrom` is already available via `using ERC20Helper for IERC20Base` at the top of `ContractOwner.sol`. [8](#0-7) 

---

### Proof of Concept

1. Identify a `subaccount` whose `directDepositV1Address` holds a non-zero usdcE balance (readable from the public mapping and `IERC20Base.balanceOf`).
2. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` from any EOA **without** approving ContractOwner to spend USDC.
3. If USDC's `transferFrom` returns `false` (non-reverting failure), execution continues:
   - `DirectDepositV1.withdraw(usdcE)` transfers the full usdcE balance to ContractOwner.
   - `usdcE.safeTransfer(msg.sender, balance)` transfers the full usdcE balance to the attacker.
4. The attacker receives usdcE; the subaccount's `DirectDepositV1` is emptied; no USDC was provided. [1](#0-0)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L38-38)
```text
    mapping(bytes32 => address payable) public directDepositV1Address;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
