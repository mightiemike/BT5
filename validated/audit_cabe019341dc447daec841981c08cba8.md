### Title
Unchecked `transferFrom` Return Value Enables Theft of usdcE from DirectDepositV1 Accounts - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the boolean return value. Because the function has no access control beyond a chain ID check, any unprivileged caller on chain 57073 (Ink) can invoke it, allow the USDC `transferFrom` to fail silently, and still receive the usdcE that is unconditionally withdrawn from the target `DirectDepositV1` account in the same call.

---

### Finding Description

`replaceUsdcEWithUsdc` is intended as a token-swap helper: the caller provides USDC and receives usdcE in exchange, while the DDA's usdcE balance is replaced with USDC. The function is gated only by a chain ID check and is otherwise callable by any address.

The critical flaw is at line 616:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

`IERC20Base.transferFrom` is declared to return `bool`:

```solidity
function transferFrom(address from, address to, uint256 amount) external returns (bool);
```

The return value is never inspected. If the call returns `false` (e.g., the caller has no USDC balance or has not approved `ContractOwner`), execution does not revert and continues to the next two statements:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));   // pulls usdcE into ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);              // sends usdcE to attacker
```

The outbound `safeTransfer` of usdcE is correctly guarded, but the inbound USDC payment is not. The net effect is that the attacker receives usdcE from the DDA while contributing nothing. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

An attacker can drain the entire usdcE balance of any `DirectDepositV1` account that has been registered under any subaccount on chain 57073. The stolen asset is usdcE (a bridged USDC variant), a real ERC-20 token with monetary value. The DDA's usdcE balance is zeroed and the attacker receives it without paying USDC. This is a direct, irreversible asset loss for the subaccount owner whose DDA is targeted. [3](#0-2) 

---

### Likelihood Explanation

The function is `external` with no `onlyOwner` or `onlyDeployer` modifier — any EOA or contract on chain 57073 can call it. The attacker needs only to identify a subaccount whose DDA holds usdcE (observable on-chain via `balanceOf`) and call `replaceUsdcEWithUsdc` without holding or approving any USDC. The precondition is trivially satisfiable. [4](#0-3) 

---

### Recommendation

Replace the raw `transferFrom` call with the project's own `safeTransferFrom` helper (already defined in `ERC20Helper`) or OpenZeppelin's `SafeERC20.safeTransferFrom`, which reverts on a `false` return value:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` already implements the correct pattern used elsewhere in the codebase: [5](#0-4) 

---

### Proof of Concept

1. Identify a subaccount `S` whose `DirectDepositV1` at `directDepositV1Address[S]` holds `N` usdcE tokens (verifiable via `IERC20Base(usdcE).balanceOf(dda)`).
2. From any EOA with zero USDC balance and zero USDC allowance to `ContractOwner`, call:
   ```solidity
   ContractOwner.replaceUsdcEWithUsdc(S);
   ```
3. Execution reaches line 616: `IERC20Base(usdc).transferFrom(attacker, dda, N)` returns `false` (insufficient balance/allowance). Return value is discarded; no revert.
4. Execution reaches line 617: `DirectDepositV1(dda).withdraw(usdcE)` — transfers `N` usdcE from the DDA to `ContractOwner`.
5. Execution reaches line 618: `IERC20Base(usdcE).safeTransfer(attacker, N)` — transfers `N` usdcE to the attacker.
6. Result: attacker holds `N` usdcE; the DDA holds 0 usdcE; no USDC was ever transferred. [6](#0-5) [7](#0-6)

### Citations

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
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
