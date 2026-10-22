const CREATORS = [
  { name: "YAEL COHEN", url: "https://github.com/yael3222664-alt" },
  { name: "MALKA LORENTS", url: "https://github.com/9697-mali-lo" },
];

const CONTACT_EMAILS = ["yael3222664@gmail.com", "6719697@gmail.com"];

export function Footer() {
  return (
    <footer className="app-footer">
      <img className="footer-logo" src="/logo.png" alt="YMCL" />
      <p className="footer-credits">
        Created by{" "}
        {CREATORS.map((creator, index) => (
          <span key={creator.name}>
            {index > 0 && " and "}
            <a href={creator.url} target="_blank" rel="noreferrer">
              {creator.name}
            </a>
          </span>
        ))}
      </p>
      <p className="footer-contact">
        {CONTACT_EMAILS.map((email, index) => (
          <span key={email}>
            {index > 0 && <span aria-hidden="true"> · </span>}
            <a href={`mailto:${email}`}>{email}</a>
          </span>
        ))}
      </p>
    </footer>
  );
}
